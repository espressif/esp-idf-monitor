# SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import re
import struct
from typing import Any, List, Optional, Tuple, Union

from elftools.elf.elffile import ELFFile

from .output_helpers import warning_print

# Examples of format:
# %d        - specifier='d'
# %10d      - width='10', specifier='d'
# %-5.2f    - flags='-', width='5', precision='2', specifier='f'
# %#08x     - flags='#0', width='8', specifier='x'
# %llX      - length='ll', specifier='X'
# %zu       - length='z', specifier='u'
# %p        - specifier='p'
PRINTF_FORMAT_REGEX = re.compile(
    r'%(?P<flags>[-+0# ]*)?'        # (1) Flags: Optional, can include '-', '+', '0', '#', or ' ' (space)
    r'(?P<width>\*|\d+)?'           # (2) Width: Optional, specifies minimum field width (e.g., "10" in "%10d")
    r'(\.(?P<precision>\*|\d+))?'   # (3) Precision: Optional, starts with '.', followed by digits (e.g., ".2" in "%.2f")
    r'(?P<length>hh|h|l|ll|z|j|t|L)?'  # (4) Length Modifier: Optional (e.g., "ll" in "%lld", "z" in "%zu")
    r'(?P<specifier>[diuoxXfFeEgGaAcsp])'  # (5) Specifier: Required (e.g., "d" for integers, "s" for strings)
)


class Control:
    FORMAT = '>H'

    def __init__(self, raw_data: bytes) -> None:
        data = struct.unpack_from(self.FORMAT, raw_data)[0]
        self.pkg_len: int = data & 0x3FF
        self.level: int = (data >> 10) & 0x07
        self.time_64bits: bool = bool((data >> 13) & 0x01)
        self.version: int = (data >> 14) & 0x03

    def size(self) -> int:
        return struct.calcsize(self.FORMAT)


class Message:
    def __init__(self, debug: bool, elf_path: str, input_data: bytes) -> None:
        self.debug = debug
        self.elf_path = elf_path
        self.buffer_hex_log = False
        self.buffer_char_log = False
        self.buffer_hexdump_log = False
        idx = 0

        self.app_type: int = input_data[idx]
        idx += 1

        self.control = Control(input_data[idx:])
        idx += self.control.size()

        self.format, i_text = self.retrieve_string(input_data[idx:])
        idx += i_text

        self.buffer_hex_log = self.format.startswith('__ESP_BUFFER_HEX_FORMAT__')
        self.buffer_char_log = self.format.startswith('__ESP_BUFFER_CHAR_FORMAT__')
        self.buffer_hexdump_log = self.format.startswith('__ESP_BUFFER_HEXDUMP_FORMAT__')
        self.tag, i_text = self.retrieve_string(input_data[idx:])
        idx += i_text

        time_fmt = '>Q' if self.control.time_64bits else '>I'
        time_size = struct.calcsize(time_fmt)
        self.timestamp = struct.unpack_from(time_fmt, input_data, idx)[0]
        idx += time_size

        self.raw_args: bytes = input_data[idx:]
        self.args: List[Union[int, str, float, bytes]] = self.retrieve_arguments(self.format, self.raw_args)

    def is_embedded_string(self, raw_args: bytes) -> bool:
        return bool((struct.unpack_from('>I', raw_args)[0] & 0xFC000000) == 0xFC000000)

    def retrieve_string(self, data: bytes) -> Tuple[str, int]:
        text, text_len = self.retrieve_data(data)
        return text.decode(errors='replace').rstrip('\x00'), text_len

    def retrieve_data(self, data: bytes, req_len: Optional[int] = None) -> Tuple[bytes, int]:
        if self.is_embedded_string(data):
            data, data_len = self.retrieve_embedded_data(data)
            data_len += 2
            return data, data_len
        addr_format = '>I'
        addr = struct.unpack_from(addr_format, data)[0]
        return self.retrieve_data_from_elf(addr, req_len), struct.calcsize(addr_format)

    def retrieve_data_from_elf(self, addr: int, req_len: Optional[int] = None) -> bytes:
        def get_data(section, addr: int) -> Optional[bytes]:
            if section['sh_addr'] <= addr < section['sh_addr'] + section['sh_size']:
                data: bytes = section.data()[addr - section['sh_addr']:]
                end_offset = req_len if req_len is not None else data.find(b'\x00')
                if len(data) and end_offset >= 0:
                    return data[:end_offset]
            return None

        if self.elf_path is None:
            return b'<no ELF file>'
        # Look up the address in the ELF file
        with open(self.elf_path, 'rb') as elf_file:
            elf = ELFFile(elf_file)
            if self.is_noload_address(addr):
                noload_section = elf.get_section_by_name('.noload')
                if noload_section is not None:
                    data = get_data(noload_section, addr)
                    if data is not None:
                        return data
            else:
                for section in elf.iter_sections():
                    data = get_data(section, addr)
                    if data is not None:
                        return data
        return f'<not found {addr:08X}>'.encode('ascii')

    def is_noload_address(self, addr: int) -> bool:
        return bool((addr & 0xFF000000) == 0)

    def retrieve_embedded_data(self, raw_args: bytes) -> Tuple[bytes, int]:
        assert self.is_embedded_string(raw_args), 'Invalid embedded data'
        data_len_format = '>h'
        data_len = 1 - struct.unpack_from(data_len_format, raw_args)[0]
        retrieved_data = struct.unpack_from(f'>{data_len}s', raw_args, struct.calcsize(data_len_format))[0]
        return retrieved_data, data_len

    def retrieve_arguments(self, format: str, raw_args: bytes) -> List[Union[int, str, float, bytes]]:
        args: List[Union[int, str, float, bytes]] = []
        i_str = 0
        i_arg = 0
        while i_str < len(format):
            match = PRINTF_FORMAT_REGEX.search(format, i_str)
            if not match:
                break
            i_str = match.end()
            length = match.group('length') or ''
            specifier = match.group('specifier')

            # **Handling String (%s, %S)**
            if specifier in 'sS':
                if self.buffer_hex_log or self.buffer_char_log or self.buffer_hexdump_log:
                    buffer_len = args[-1] if isinstance(args[-1], int) else None  # Previous argument is the buffer length
                    data, data_len = self.retrieve_data(raw_args[i_arg:], buffer_len)
                    args.append(data)
                else:
                    text, data_len = self.retrieve_string(raw_args[i_arg:])
                    args.append(text)
                i_arg += data_len

            # **Handling Floating Point (%f, %e, %g, etc.)**
            elif specifier in 'fFeEgGaA':
                arg_format = '>d'
                args.append(struct.unpack_from(arg_format, raw_args, i_arg)[0])
                i_arg += struct.calcsize(arg_format)

            # **Handling Length-Prefixed Integers**
            elif specifier in 'diouxX':
                # **ESP32 sends only 32-bit or 64-bit values**
                if length == 'll':  # 64-bit integers
                    fmt = 'q'
                else:  # 32-bit integers
                    if length == 'h':  # short
                        fmt = 'h'
                        i_arg += 2
                    elif length == 'hh':  # char
                        fmt = 'b'
                        i_arg += 3
                    else:  # int
                        fmt = 'i'
                fmt = fmt.lower() if specifier in 'di' else fmt.upper()  # Signed or unsigned
                value = struct.unpack_from(f'>{fmt}', raw_args, i_arg)[0]
                i_arg += struct.calcsize(fmt)
                args.append(value)

            # **Handling Pointer (%p)**
            elif specifier == 'p':
                arg_format = '>I'
                args.append(struct.unpack_from(arg_format, raw_args, i_arg)[0])
                i_arg += struct.calcsize(arg_format)

            # **Handling Characters (%c)**
            elif specifier == 'c':
                i_arg += 3
                arg_format = '>c'
                args.append(struct.unpack_from(arg_format, raw_args, i_arg)[0].decode(errors='replace'))
                i_arg += struct.calcsize(arg_format)

            # **Default: Treat as integer (%d, %i)**
            else:
                arg_format = '>i'
                val = struct.unpack_from(arg_format, raw_args, i_arg)[0]
                args.append(val)
                i_arg += struct.calcsize(arg_format)
        return args


class BinaryLog:
    def __init__(self, elf_paths: list) -> None:
        if elf_paths is None or len(elf_paths) == 0:
            warning_print('No ELF files found. Please provide the ELF file paths, required for binary log decoding.')
        self.elf_paths = elf_paths
        self.debug = False

    def detected(self, line: int) -> bool:
        return line in (0x01, 0x02)

    def source_of_message(self, first_byte: int) -> Any:
        if not self.detected(first_byte):
            return None
        if self.elf_paths == []:
            return None
        if first_byte == 0x01:
            for elf_path in self.elf_paths:
                if elf_path.endswith('bootloader.elf'):
                    return elf_path
            return None
        return self.elf_paths[0]  # Default to app

    @staticmethod
    def crc8(data: bytes) -> int:
        crc = 0  # Initial CRC value
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x07
                else:
                    crc <<= 1
            crc &= 0xFF  # Ensure it's within 8 bits
        return crc

    def find_frames(self, data: bytes) -> Tuple[List[bytes], bytes]:
        frames: List[bytes] = []
        i = 0
        idx_of_last_found_pkg = 0
        while i < len(data):
            if len(data[i:]) < 15:  # Minimal frame len
                break
            if self.detected(int(data[i])):
                start_idx = i
                control = Control(data[start_idx + 1:])
                if control.pkg_len > len(data[i:]):
                    break
                frame = data[start_idx:start_idx + control.pkg_len]
                if control.pkg_len != 0 and self.crc8(frame) == 0:
                    frames.append(frame)
                    idx_of_last_found_pkg = start_idx + control.pkg_len
                    i += control.pkg_len - 1
            i += 1
        # Return recognized frames and any remaining unprocessed data
        return frames, data[idx_of_last_found_pkg:]

    def convert_to_text(self, data: bytes) -> Tuple[List[bytes], bytes]:
        messages: List[bytes] = [b'']
        frames, incomplete_fragment = self.find_frames(data)
        for pkg_msg in frames:
            elf_path = self.source_of_message(pkg_msg[0])
            msg = Message(self.debug, elf_path, pkg_msg)
            if msg.buffer_hex_log or msg.buffer_char_log or msg.buffer_hexdump_log:
                messages += self.format_buffer_message(msg)
            else:
                messages.append(self.format_message(msg))
        return messages, incomplete_fragment

    @staticmethod
    def convert_c_format_to_pythonic(fmt: str) -> str:
        """Convert C printf-style % formatting to Python {} formatting using a common regex."""

        def replace_match(m):
            """Helper function to convert printf specifiers to Python format."""
            flags = m.group('flags') or ''
            width = m.group('width') or ''
            precision = m.group('precision') or ''
            specifier = m.group('specifier')

            # Convert printf flags to Python equivalents
            python_flags = ''
            if '-' in flags:
                python_flags += '<'   # Left-align
            elif '0' in flags:
                python_flags += '0'   # Zero-padding
            if '+' in flags:
                python_flags += '+'   # Force sign
            elif ' ' in flags:
                python_flags += ' '   # Space before positive numbers
            if '#' in flags and specifier in 'oxX':  # Ensure correct alternate form
                python_flags += '#'
                if width and specifier == 'o':  # If width is specified, increase it by 1 to compensate for `0o` -> `0`
                    width = str(int(width) + 1)

            # Convert precision for integers (`%.5d` -> `{:05d}`)
            if precision and specifier in 'diouxX':
                width = precision  # Precision becomes width for zero-padding
                python_flags = '0'  # Force zero-padding
                precision = None    # Remove precision (Python does not support it for ints)

            # Handle width (`*` becomes `{}` placeholder)
            width_placeholder = '*' if width == '*' else width

            # Convert specifier
            python_specifier = specifier
            if specifier in 'diu':   # Integer
                python_specifier = 'd'
            elif specifier in 'o':   # Octal
                python_specifier = 'o'
            elif specifier in 'xX':  # Hexadecimal
                python_specifier = 'x' if specifier == 'x' else 'X'
            elif specifier in 'fFeEgGaA':  # Floating-point
                python_specifier = specifier.lower()
            elif specifier in 'c':   # Character
                python_specifier = 's'  # Convert `%c` to `{s}` (needs manual conversion)
            elif specifier in 's':   # String
                python_specifier = 's'
            elif specifier in 'p':   # Pointer
                python_specifier = '#x'

            # Construct final Python format specifier
            python_format = '{:' + python_flags
            if width_placeholder:
                python_format += width_placeholder
            python_format += python_specifier + '}'

            return python_format

        # Convert printf format specifiers to Python format specifiers
        return PRINTF_FORMAT_REGEX.sub(replace_match, fmt)

    @staticmethod
    def post_process_pythonic_format(formatted_message: str) -> str:
        """Fix specific formatting issues after conversion."""
        # Fix octal formatting (`0o377` → `0377`)
        formatted_message = formatted_message.replace('0o', '0')
        return formatted_message

    def format_message(self, message: Message) -> bytes:
        text_msg = self.convert_c_format_to_pythonic(message.format).format(*message.args)
        text_msg = self.post_process_pythonic_format(text_msg)
        level_name = {1: 'E', 2: 'W', 3: 'I', 4: 'D', 5: 'V'}[message.control.level]
        return f'{level_name} ({message.timestamp}) {message.tag}: {text_msg}\n'.encode('ascii')

    def format_buffer_message(self, message) -> List[bytes]:
        text_msg: List[bytes] = [b'']
        BYTES_PER_LINE = 16
        buff_len = message.args[0]
        buffer = message.args[1]
        buffer_addr = message.args[2]
        message.args = []
        if message.buffer_hex_log:
            # I (954) log_example: 54 68 65 20 77 61 79 20 74 6f 20 67 65 74 20 73
            # I (962) log_example: 74 61 72 74 65 64 20 69 73 20 74 6f 20 71 75 69
            while buff_len > 0:
                tmp_len = min(BYTES_PER_LINE, buff_len)
                message.format = ' '.join(map(lambda b: f'{b:02x}', buffer[:tmp_len]))
                text_msg.append(self.format_message(message))
                buffer = buffer[tmp_len:]
                buff_len -= tmp_len

        elif message.buffer_char_log:
            # I (980) log_example: The way to get s
            # I (985) log_example: tarted is to qui
            while buff_len > 0:
                tmp_len = min(BYTES_PER_LINE, buff_len)
                message.format = ''.join(chr(b) if 32 <= b < 127 else '' for b in buffer[:tmp_len])
                text_msg.append(self.format_message(message))
                buffer = buffer[tmp_len:]
                buff_len -= tmp_len

        elif message.buffer_hexdump_log:
            # I (1013) log_example: 0x3ffb5bc0   54 68 65 20 77 61 79 20  74 6f 20 67 65 74 20 73  |The way to get s|
            # I (1024) log_example: 0x3ffb5bd0   74 61 72 74 65 64 20 69  73 20 74 6f 20 71 75 69  |tarted is to qui|
            while buff_len > 0:
                tmp_len = min(BYTES_PER_LINE, buff_len)
                hex_part = ' '.join(f'{b:02X}' for b in buffer[:tmp_len])
                hex_part_split = ' '.join([hex_part[:24], hex_part[24:]])
                char_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in buffer[:tmp_len])
                message.format = f'0x{buffer_addr:08x}   {hex_part_split:<48}  |{char_part}|'
                text_msg.append(self.format_message(message))
                buffer = buffer[tmp_len:]
                buffer_addr += tmp_len
                buff_len -= tmp_len

        return text_msg
