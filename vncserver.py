import select
import socket
import threading
import time

from vncpixel import NATIVE_FORMAT, PixelFormat


def _full_rect(width, height):
    return (0, 0, width, height)


def _empty_rect():
    return (0, 0, 0, 0)


def _union_rect(first, second):
    if first[2] == 0:
        return second
    if second[2] == 0:
        return first
    left = min(first[0], second[0])
    top = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    bottom = max(first[1] + first[3], second[1] + second[3])
    return left, top, right - left, bottom - top


def _diff_rect(previous, current, width, height):
    """Find a safe changed-pixel bounding box without per-client scanning."""
    if len(previous) != len(current) or len(current) != width * height * 4:
        return _full_rect(width, height)

    old_view = memoryview(previous)
    new_view = memoryview(current)
    row_bytes = width * 4
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    changed_rows = 0

    for y in range(height):
        row_start = y * row_bytes
        old_row = old_view[row_start:row_start + row_bytes]
        new_row = new_view[row_start:row_start + row_bytes]
        if old_row == new_row:
            continue

        changed_rows += 1
        # For a substantially changed frame, the full rectangle avoids doing
        # a second expensive pixel scan while remaining wire-correct.
        if changed_rows > max(32, height // 4):
            return _full_rect(width, height)

        row_min = width
        row_max = -1
        for x in range(width):
            offset = x * 4
            if (old_row[offset] != new_row[offset] or
                    old_row[offset + 1] != new_row[offset + 1] or
                    old_row[offset + 2] != new_row[offset + 2] or
                    old_row[offset + 3] != new_row[offset + 3]):
                row_min = min(row_min, x)
                row_max = x
        if row_max >= 0:
            min_x = min(min_x, row_min)
            max_x = max(max_x, row_max)
            min_y = min(min_y, y)
            max_y = max(max_y, y)

    if max_x < 0:
        return _empty_rect()
    return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1


class VNCServer:
    # Fallback also supports lightweight __new__ test fixtures.
    _frame_lock = threading.RLock()

    class VNCServerThreadParameters:
        def __init__(self):
            self.vs = None  # VNCServer
            self.port = 0

    class VNCSession:
        def __init__(self):
            self.stream_lock = threading.Lock()
            self.stream = -1
            self.frame_requested = False
            self.incremental = False
            self.sent_frame_version = None
            self.pixel_format = NATIVE_FORMAT

    def __init__(self, display, kb, port, compatible):
        self._thread = None
        self._display = display
        self._kb = kb  # Keyboard
        self._listen_port = port
        self._compatible = compatible
        self._compatible_width = 640
        self._compatible_height = 400
        self._frame_lock = threading.RLock()
        self._frame_cache = None
        self._frame_cache_version = None
        self._frame_snapshot = None
        self._frame_history = []

        self._key_map = dict()
        self._key_map[0xff1b] = ( 0x01, )  # escape
        self._key_map[0xff0d] = ( 0x1c, )  # enter
        self._key_map[0xff08] = ( 0x0e, )  # backspace
        self._key_map[0xff09] = ( 0x0f, )  # tab
        self._key_map[0xffe1] = ( 0x2a, )  # left shift
        self._key_map[0xffe3] = ( 0x1d, )  # left control
        self._key_map[0xffe9] = ( 0x38, )  # left alt
        self._key_map[0xffbe] = ( 0x3b, )  # F1
        self._key_map[0xffbf] = ( 0x3c, )  # F2
        self._key_map[0xffc0] = ( 0x3d, )  # F3
        self._key_map[0xffc1] = ( 0x3e, )  # F4
        self._key_map[0xffc2] = ( 0x3f, )  # F5
        self._key_map[0xffc3] = ( 0x40, )  # F6
        self._key_map[0xffc4] = ( 0x41, )  # F7
        self._key_map[0xffc5] = ( 0x42, )  # F8
        self._key_map[0xffc6] = ( 0x43, )  # F9
        self._key_map[0xffc7] = ( 0x44, )  # F10
        self._key_map[0x31] = ( 0x02, )  # 1
        self._key_map[0x32] = ( 0x03, )
        self._key_map[0x33] = ( 0x04, )
        self._key_map[0x34] = ( 0x05, )
        self._key_map[0x35] = ( 0x06, )
        self._key_map[0x36] = ( 0x07, )
        self._key_map[0x37] = ( 0x08, )
        self._key_map[0x38] = ( 0x09, )
        self._key_map[0x39] = ( 0x0a, )  # 9
        self._key_map[0x30] = ( 0x0b, )  # 0
        self._key_map[0x41] = ( 0x1e, )  # A
        self._key_map[0x42] = ( 0x30, )
        self._key_map[0x43] = ( 0x2e, )
        self._key_map[0x44] = ( 0x20, )
        self._key_map[0x45] = ( 0x12, )
        self._key_map[0x46] = ( 0x21, )
        self._key_map[0x47] = ( 0x22, )
        self._key_map[0x48] = ( 0x23, )
        self._key_map[0x49] = ( 0x17, )
        self._key_map[0x4a] = ( 0x24, )
        self._key_map[0x4b] = ( 0x25, )
        self._key_map[0x4c] = ( 0x26, )
        self._key_map[0x4d] = ( 0x32, )
        self._key_map[0x4e] = ( 0x31, )
        self._key_map[0x4f] = ( 0x18, )
        self._key_map[0x50] = ( 0x19, )
        self._key_map[0x51] = ( 0x10, )
        self._key_map[0x52] = ( 0x13, )
        self._key_map[0x53] = ( 0x1f, )
        self._key_map[0x54] = ( 0x14, )
        self._key_map[0x55] = ( 0x16, )
        self._key_map[0x56] = ( 0x2f, )
        self._key_map[0x57] = ( 0x11, )
        self._key_map[0x58] = ( 0x2d, )
        self._key_map[0x59] = ( 0x15, )
        self._key_map[0x5a] = ( 0x2c, )  # Z
        self._key_map[0x61] = ( 0x1e, )  # a
        self._key_map[0x62] = ( 0x30, )
        self._key_map[0x63] = ( 0x2e, )
        self._key_map[0x64] = ( 0x20, )
        self._key_map[0x65] = ( 0x12, )
        self._key_map[0x66] = ( 0x21, )
        self._key_map[0x67] = ( 0x22, )
        self._key_map[0x68] = ( 0x23, )
        self._key_map[0x69] = ( 0x17, )
        self._key_map[0x6a] = ( 0x24, )
        self._key_map[0x6b] = ( 0x25, )
        self._key_map[0x6c] = ( 0x26, )
        self._key_map[0x6d] = ( 0x32, )
        self._key_map[0x6e] = ( 0x31, )
        self._key_map[0x6f] = ( 0x18, )
        self._key_map[0x70] = ( 0x19, )
        self._key_map[0x71] = ( 0x10, )
        self._key_map[0x72] = ( 0x13, )
        self._key_map[0x73] = ( 0x1f, )
        self._key_map[0x74] = ( 0x14, )
        self._key_map[0x75] = ( 0x16, )
        self._key_map[0x76] = ( 0x2f, )
        self._key_map[0x77] = ( 0x11, )
        self._key_map[0x78] = ( 0x2d, )
        self._key_map[0x79] = ( 0x15, )
        self._key_map[0x7a] = ( 0x2c, )  # z
        self._key_map[0x20] = ( 0x39, )  # space
        self._key_map[0x22] = ( 0x28, )  # "
        self._key_map[0x2c] = ( 0x33, )  # ,
        self._key_map[0x2e] = ( 0x34, )  # .
        self._key_map[0x25] = ( 0x06, )  # %
        self._key_map[0x24] = ( 0x05, )  # $
        self._key_map[0x2d] = ( 0x0c, )  # -
        self._key_map[0x5f] = ( 0x0c, )  # _
        self._key_map[0x3a] = ( 0x27, )  # :
        self._key_map[0x2f] = ( 0x35, )  # /
        self._key_map[0x3f] = ( 0x35, )  # ?
        self._key_map[0x2a] = ( 0x09, )  # *  (shift)
        self._key_map[0x26] = ( 0x08, )  # &  (shift)
        self._key_map[0x40] = ( 0x03, )  # @
        self._key_map[0x5c] = ( 0x2b, )  # \
        self._key_map[0x7c] = ( 0x2b, )  # |  (shift)
        self._key_map[0x3d] = ( 0x0d, )  # =
        self._key_map[0xff54] = ( 0x50, )  # cursor down
        self._key_map[0xff52] = ( 0x48, )  # cursor up
        self._key_map[0xff51] = ( 0x4b, )  # cursor left
        self._key_map[0xff53] = ( 0x4d, )  # cursor right
        self._key_map[0xff50] = ( 0x47, )  # home
        self._key_map[0xff57] = ( 0x4f, )  # end
        self._key_map[0xff56] = ( 0xe0, 0x51 )  # page down
        self._key_map[0xff55] = ( 0xe0, 0x49 )  # page up
        self._key_map[0xff14] = ( 0x46, )  # Scroll Lock (X11 keysym -> XT scan code)


        _thread = threading.Thread(target=self.VNCServerThread, args=(port, ))
        _thread.daemon = True
        _thread.name = "vnc-server-thread"
        _thread.start()

    def _get_frame(self):
        with self._frame_lock:
            return self._get_frame_locked()

    def _get_frame_locked(self):
        """Render once per visible display version, shared by all sessions."""
        version_getter = getattr(self._display, 'GetFrameVersion', None)
        if not callable(version_getter):
            return self._display.GetFrame()
        version = version_getter()
        if getattr(self, '_frame_cache_version', None) != version:
            previous = getattr(self, '_frame_snapshot', None)
            self._frame_cache = self._display.GetFrame()
            self._frame_cache_version = version
            width, height, pixels = self._frame_cache
            rect = (_full_rect(width, height) if previous is None else
                    _diff_rect(previous, pixels, width, height))
            history = getattr(self, '_frame_history', None)
            if history is None:
                history = self._frame_history = []
            history.append((version, rect))
            if len(history) > 128:
                del history[:-128]
            self._frame_snapshot = bytes(pixels)
        return self._frame_cache

    def _rect_since(self, version, current_version, width, height):
        """Union rendered changes after a session's last sent version."""
        if version is None:
            return _full_rect(width, height)
        if version == current_version:
            return _empty_rect()

        history = getattr(self, '_frame_history', ())
        start = None
        for index, (item_version, _) in enumerate(history):
            if item_version == version:
                start = index
                break
        if start is None:
            return _full_rect(width, height)

        result = _empty_rect()
        found_current = False
        for item_version, rect in history[start + 1:]:
            result = _union_rect(result, rect)
            if item_version == current_version:
                found_current = True
                break
        return result if found_current else _full_rect(width, height)

    @staticmethod
    def _crop_frame(pixels, frame_width, rect):
        x, y, width, height = rect
        try:
            source = memoryview(pixels).cast('B')
        except TypeError:
            source = memoryview(bytes(pixels))
        if x == 0 and width == frame_width:
            start = y * frame_width * 4
            return source[start:start + height * frame_width * 4]
        row_bytes = width * 4
        source_row_bytes = frame_width * 4
        cropped = bytearray(row_bytes * height)
        for row in range(height):
            source_start = (y + row) * source_row_bytes + x * 4
            target_start = row * row_bytes
            cropped[target_start:target_start + row_bytes] = source[
                source_start:source_start + row_bytes]
        return cropped

    def PushChar(self, c, press):
        if self._kb == None:
            return

        if c in self._key_map:
            for m in self._key_map[c]:
                self._kb.PushKeyboardScancode(m if press else (m | 0x80))

    @staticmethod
    def RecvExact(stream, length):
        buffer = bytearray()
        while len(buffer) < length:
            chunk = stream.recv(length - len(buffer))
            if not chunk:
                raise ConnectionError('VNC client closed the connection')
            buffer.extend(chunk)
        return bytes(buffer)

    def VNCSendVersion(self, stream):
        msg = "RFB 003.008\n".encode('ascii')
        stream.sendall(msg)

        # wait for reply, ignoring what it is
        while True:
            buffer = self.RecvExact(stream, 1)[0]
            print(f'{buffer:c}', end='')
            if buffer == ord('\n'):
                break
        print()

    def VNCSecurityHandshake(self, stream):
        list_ = (1, 1)  # 1, None
        stream.sendall(bytes(list_))

        # receive reply with choice, ignoring choice
        self.RecvExact(stream, 1)

        reply = [ 0 ] * 4
        stream.sendall(bytes(reply))

    def VNCClientServerInit(self, stream):
        self.RecvExact(stream, 1)

        example = self._get_frame()
        width = self._compatible_width if self._compatible else example[0]
        height = self._compatible_height if self._compatible else example[1]
        reply = [ 0 ] * 24
        reply[0] = width >> 8
        reply[1] = width & 255
        reply[2] = height >> 8
        reply[3] = height & 255
        reply[4:20] = NATIVE_FORMAT.to_bytes()
        name = 'PyPC'
        name_bytes = name.encode('ascii')
        reply[20] = (len(name_bytes) >> 24) & 255
        reply[21] = (len(name_bytes) >> 16) & 255
        reply[22] = (len(name_bytes) >>  8) & 255
        reply[23] = len(name_bytes) & 255
        stream.sendall(bytes(reply))
        stream.sendall(name_bytes)

    def VNCWaitForEvent(self, session):
        try:
            # select.poll() is unavailable on Windows; select.select() works
            # for the socket used by the VNC session on all supported hosts.
            readable, _, _ = select.select([session.stream], [], [], 1 / 60)
            if len(readable) == 0:
                return True

            type_ = self.RecvExact(session.stream, 1)[0]

            if type_ == 0:  # SetPixelFormat
                self.RecvExact(session.stream, 3)  # padding
                session.pixel_format = PixelFormat.from_bytes(
                    self.RecvExact(session.stream, 16))
                session.sent_frame_version = None
            elif type_ == 2:  # SetEncodings
                temp = self.RecvExact(session.stream, 3)

                no_encodings = (temp[1] << 8) | temp[2]
                print(f'VNC: retrieve {no_encodings} encodings')
                for i in range(no_encodings):
                    encoding = self.RecvExact(session.stream, 4)
                    e = int.from_bytes(encoding, 'big', signed=True)
                    print(f'VNC: retrieved encoding {i}: {e}')
                    if e == -259:
                        print("VNC client supports audio")
                        session.audio_enabled = True
            elif type_ == 3:  # FramebufferUpdateRequest
                request = self.RecvExact(session.stream, 9)
                session.incremental = bool(request[0])
                session.frame_requested = True
            elif type_ == 4:  # KeyEvent
                buffer = self.RecvExact(session.stream, 7)
                vnc_scan_code = (buffer[3] << 24) | (buffer[4] << 16) | (buffer[5] << 8) | buffer[6]
                print(f'Key {buffer[0]} {vnc_scan_code:04x}')
                self.PushChar(vnc_scan_code, buffer[0] != 0)
            elif type_ == 5:  # PointerEvent
                self.RecvExact(session.stream, 5)
            elif type_ == 6:  # ClientCutText
                buffer = self.RecvExact(session.stream, 7)
                n_to_read = (buffer[3] << 24) | (buffer[4] << 16) | (buffer[5] << 8) | buffer[6]
                self.RecvExact(session.stream, n_to_read)
            else:
                print(f'VNC: Client message {type_} not understood')
                return False

            return True

        except Exception as e:
            print(f'VNCWaitForEvent exception: {e}, line number: {e.__traceback__.tb_lineno}')

        return False

    def VNCSendFrame(self, session):
        # Sample/version/render/history lookup are one shared-cache transaction.
        # Network writes stay outside the lock so a slow viewer cannot block others.
        with self._frame_lock:
            frame = self._get_frame_locked()
            frame_version = getattr(self, '_frame_cache_version', None)
            if session.incremental and frame_version is not None and not self._compatible:
                changed = self._rect_since(session.sent_frame_version, frame_version,
                                           frame[0], frame[1])
            else:
                changed = _full_rect(frame[0], frame[1])

        if changed[2] == 0:
            session.sent_frame_version = frame_version
            with session.stream_lock:
                session.stream.sendall(b'\x00\x00\x00\x00')
            return

        width = self._compatible_width if self._compatible else changed[2]
        height = self._compatible_height if self._compatible else changed[3]
        update = [0] * (4 + 12)
        update[0] = 0  # FrameBufferUpdate
        update[1] = 0  # padding
        update[2] = 0  # one rectangle
        update[3] = 1
        update[4] = 0 if self._compatible else changed[0] >> 8
        update[5] = 0 if self._compatible else changed[0] & 255
        update[6] = 0 if self._compatible else changed[1] >> 8
        update[7] = 0 if self._compatible else changed[1] & 255
        update[8] = width >> 8
        update[9] = width & 255
        update[10] = height >> 8
        update[11] = height & 255
        update[12] = 0
        update[13] = 0
        update[14] = 0
        update[15] = 0

        if self._compatible and (width != frame[0] or height != frame[1]):
            buffer = bytearray(width * height * 4)
            use_width = min(width, frame[0])
            use_height = min(height, frame[1])
            for y in range(use_height):
                in_offset = y * frame[0] * 4
                out_offset = y * width * 4
                buffer[out_offset:out_offset + use_width * 4] = frame[2][
                    in_offset:in_offset + use_width * 4]
            payload = buffer
        elif self._compatible:
            payload = frame[2]
        else:
            payload = self._crop_frame(frame[2], frame[0], changed)

        with session.stream_lock:
            session.stream.sendall(bytes(update))
            session.stream.sendall(session.pixel_format.encode_bgra(payload))
        session.sent_frame_version = frame_version

    def VNCClientThread(self, session):
        try:
            self.VNCSendVersion(session.stream)
            self.VNCSecurityHandshake(session.stream)
            self.VNCClientServerInit(session.stream)
            session.frame_requested = True

            last_frame_time = 0.0
            frame_interval = 1.0 / 20.0
            while True:
                now = time.monotonic()
                if (session.frame_requested and
                        now - last_frame_time >= frame_interval):
                    self.VNCSendFrame(session)
                    session.frame_requested = False
                    last_frame_time = now

                if self.VNCWaitForEvent(session) == False:
                    break
        except Exception as e:
            print(f'VNCClientThread exception: {e}, line number: {e.__traceback__.tb_lineno}')

        session.stream.close()

    def VNCServerThread(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', port))
        s.listen(128)
        print(f'Listening on port {port} for a VNC session')

        while True:
            client, c_addr = s.accept()
            print(f'VNC server connected to {c_addr}')

            session = self.VNCSession()
            session.stream_lock = threading.Lock()
            session.stream = client

            t = threading.Thread(target=self.VNCClientThread, args=(session,))
            t.daemon = True
            t.name = 'VNC client'
            t.start()
