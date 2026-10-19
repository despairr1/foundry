"""A real pseudo-terminal for the Terminal pane: Windows ConPTY through ctypes (Windows 10 1809 and later), or
the pty module elsewhere. No extra packages.

    term = PseudoTerminal(["pwsh.exe", "-NoLogo"], cwd, cols=100, rows=30)
    term.start(on_output=lambda text: ..., on_exit=lambda code: ...)   # callbacks run on a reader thread
    term.write("dir\\r"); term.resize(120, 40); term.kill()

Output is decoded UTF-8 text full of VT escape sequences; vt_screen.Screen turns it into a screen."""
import codecs
import os
import shutil
import subprocess
import sys
import threading

IS_WINDOWS = sys.platform == "win32"


def default_shell():
    """The shell from Settings › Terminal (TERMINAL_SHELL): PowerShell 7 if installed, else Windows PowerShell;
    the user's shell elsewhere."""
    if IS_WINDOWS:
        choice = os.environ.get("TERMINAL_SHELL", "auto").strip().lower()
        if choice == "cmd":
            return [shutil.which("cmd.exe") or "cmd.exe"]
        if choice == "powershell":
            return [shutil.which("powershell.exe") or "powershell.exe", "-NoLogo"]
        if choice not in ("", "auto", "pwsh") and os.path.isfile(choice):
            return [choice]
        pwsh = shutil.which("pwsh.exe")
        if choice == "pwsh" and not pwsh:
            choice = "auto"
        return [pwsh or shutil.which("powershell.exe") or "powershell.exe", "-NoLogo"]
    return [os.environ.get("SHELL") or "/bin/bash", "-i"]


def command_line_for(command):
    """argv that runs one command in the shell, prints it first, and exits with its exit code."""
    if IS_WINDOWS:
        import base64
        shown = command.replace("'", "''")
        script = (f"Write-Host -ForegroundColor DarkGray '> {shown}'\n{command}\n"
                  "$ok = $?\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\nif (-not $ok) { exit 1 }\nexit 0\n")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"
        return [shell, "-NoLogo", "-NoProfile", "-EncodedCommand", encoded]
    return ["/bin/bash", "-lc", f"printf '\\033[90m> %s\\033[0m\\n' {_sh_quote(command)}; {command}"]


def _sh_quote(text):
    return "'" + text.replace("'", "'\"'\"'") + "'"


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _COORD(ctypes.Structure):
        _fields_ = [("X", wt.SHORT), ("Y", wt.SHORT)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                    ("hStdError", wt.HANDLE)]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    _STARTF_USESTDHANDLES = 0x00000100
    _INFINITE = 0xFFFFFFFF

    _k32.CreatePipe.argtypes = [ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.c_void_p, wt.DWORD]
    _k32.CreatePipe.restype = wt.BOOL
    _k32.CreatePseudoConsole.argtypes = [_COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    _k32.CreatePseudoConsole.restype = ctypes.c_long
    _k32.ResizePseudoConsole.argtypes = [wt.HANDLE, _COORD]
    _k32.ResizePseudoConsole.restype = ctypes.c_long
    _k32.ClosePseudoConsole.argtypes = [wt.HANDLE]
    _k32.ClosePseudoConsole.restype = None
    _k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    _k32.InitializeProcThreadAttributeList.restype = wt.BOOL
    _k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.c_void_p, ctypes.c_void_p]
    _k32.UpdateProcThreadAttribute.restype = wt.BOOL
    _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _k32.DeleteProcThreadAttributeList.restype = None
    _k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                    ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_PROCESS_INFORMATION)]
    _k32.CreateProcessW.restype = wt.BOOL
    _k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.ReadFile.restype = wt.BOOL
    _k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.WriteFile.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _k32.GetExitCodeProcess.restype = wt.BOOL
    _k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    _k32.TerminateProcess.restype = wt.BOOL


class PseudoTerminal:
    """One program running in a pseudo-terminal of cols x rows."""

    def __init__(self, argv, cwd=None, cols=100, rows=30, env=None):
        self.argv = list(argv)
        self.cwd = cwd if cwd and os.path.isdir(cwd) else None
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        self.env = env
        self.exit_code = None
        self.pid = None
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ start
    def start(self, on_output, on_exit):
        """on_output(text) for every chunk; on_exit(code) once, after the last output. Both on a background thread."""
        self._on_output, self._on_exit = on_output, on_exit
        if IS_WINDOWS:
            self._start_windows()
        else:
            self._start_posix()

    def _start_windows(self):
        in_read, in_write, out_read, out_write = wt.HANDLE(), wt.HANDLE(), wt.HANDLE(), wt.HANDLE()
        if not _k32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0) or \
                not _k32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
            raise OSError(ctypes.get_last_error(), "CreatePipe failed")
        self._hpc = wt.HANDLE()
        hr = _k32.CreatePseudoConsole(_COORD(self.cols, self.rows), in_read, out_write, 0, ctypes.byref(self._hpc))
        # The pseudo-console has its own copies of its ends of the pipes.
        _k32.CloseHandle(in_read)
        _k32.CloseHandle(out_write)
        if hr < 0:
            _k32.CloseHandle(in_write)
            _k32.CloseHandle(out_read)
            raise OSError(hr, "This version of Windows has no pseudo-console (needs Windows 10 1809 or later)")
        self._in, self._out = in_write, out_read

        size = ctypes.c_size_t(0)
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attrs = (ctypes.c_byte * size.value)()
        if not _k32.InitializeProcThreadAttributeList(self._attrs, 1, 0, ctypes.byref(size)) or \
                not _k32.UpdateProcThreadAttribute(self._attrs, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self._hpc.value,
                                                   ctypes.sizeof(wt.HANDLE), None, None):
            self._close_console()
            raise OSError(ctypes.get_last_error(), "Couldn't attach the pseudo-console")
        info = _STARTUPINFOEXW()
        info.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        # Without this the child can inherit this process's own (redirected) std handles instead of the terminal.
        info.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        info.lpAttributeList = ctypes.cast(self._attrs, ctypes.c_void_p)
        proc = _PROCESS_INFORMATION()
        flags = _EXTENDED_STARTUPINFO_PRESENT
        env_block = None
        if self.env is not None:
            flags |= _CREATE_UNICODE_ENVIRONMENT
            env_block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in self.env.items()) + "\0")
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.argv))
        if not _k32.CreateProcessW(None, command, None, None, False, flags, env_block, self.cwd, ctypes.byref(info),
                                   ctypes.byref(proc)):
            error = ctypes.get_last_error()
            self._close_console()
            raise OSError(error, f"Couldn't start {self.argv[0]}")
        _k32.CloseHandle(proc.hThread)
        self._process, self.pid = proc.hProcess, proc.dwProcessId
        threading.Thread(target=self._read_windows, daemon=True).start()
        threading.Thread(target=self._wait_windows, daemon=True).start()

    def _read_windows(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ctypes.create_string_buffer(16384)
        read = wt.DWORD()
        while _k32.ReadFile(self._out, buffer, len(buffer), ctypes.byref(read), None) and read.value:
            text = decoder.decode(buffer.raw[:read.value])
            if text:
                self._on_output(text)
        # The pipe closes once the program has exited and the pseudo-console is closed: all output is in.
        _k32.CloseHandle(self._out)
        _k32.DeleteProcThreadAttributeList(self._attrs)
        self._on_exit(self.exit_code)

    def _wait_windows(self):
        _k32.WaitForSingleObject(self._process, _INFINITE)
        code = wt.DWORD()
        _k32.GetExitCodeProcess(self._process, ctypes.byref(code))
        self.exit_code = code.value if code.value < 0x80000000 else code.value - 0x100000000
        _k32.CloseHandle(self._process)
        self._close_console()  # flushes the last output and ends the reader

    def _close_console(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        _k32.ClosePseudoConsole(self._hpc)
        _k32.CloseHandle(self._in)

    def _start_posix(self):
        import pty
        master, slave = pty.openpty()
        self._set_posix_size(slave)
        self._proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdin=slave, stdout=slave, stderr=slave,
                                      start_new_session=True, close_fds=True)
        os.close(slave)
        self._master, self.pid = master, self._proc.pid
        threading.Thread(target=self._read_posix, daemon=True).start()

    def _read_posix(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                data = os.read(self._master, 16384)
            except OSError:
                break
            if not data:
                break
            self._on_output(decoder.decode(data))
        self.exit_code = self._proc.wait()
        os.close(self._master)
        self._on_exit(self.exit_code)

    def _set_posix_size(self, fd):
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    # ---------------------------------------------------------------- control
    def write(self, text):
        data = text.encode("utf-8")
        if not data:
            return
        try:
            if IS_WINDOWS:
                with self._lock:
                    if self._closed:
                        return
                    written = wt.DWORD()
                    _k32.WriteFile(self._in, data, len(data), ctypes.byref(written), None)
            else:
                os.write(self._master, data)
        except OSError:
            pass

    def resize(self, cols, rows):
        cols, rows = max(2, int(cols)), max(2, int(rows))
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        try:
            if IS_WINDOWS:
                with self._lock:
                    if not self._closed:
                        _k32.ResizePseudoConsole(self._hpc, _COORD(cols, rows))
            else:
                self._set_posix_size(self._master)
        except OSError:
            pass

    @property
    def running(self):
        return self.pid is not None and self.exit_code is None

    def kill(self):
        """Ends the program and everything it started."""
        if not self.running:
            return
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)], stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGKILL)
            except OSError:
                pass
