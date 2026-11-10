using System.IO.Pipes;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Tools.Types;

internal static class DesktopConnection
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Credentials { public int Pid; public uint Uid; public uint Gid; }
    [DllImport("libc", SetLastError = true)]
    private static extern uint geteuid();
    [DllImport("libc", SetLastError = true)]
    private static extern int getsockopt(int socket, int level, int option, out Credentials value, ref uint length);

    internal static string LinuxSocketPath()
    {
        var explicitPath = Environment.GetEnvironmentVariable("CONTEXT_KEEPER_IPC_SOCKET");
        if (!string.IsNullOrWhiteSpace(explicitPath)) return Path.GetFullPath(explicitPath);
        var home = Environment.GetEnvironmentVariable("CODEX_HOME") ??
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".codex");
        var directory = Path.GetFullPath(Path.Combine(home, "ipc"));
        var path = Path.Combine(directory, "ipc.sock");
        if (Encoding.UTF8.GetByteCount(path) <= 107) return path;
        var info = new DirectoryInfo(directory);
        var canonical = info.ResolveLinkTarget(true)?.FullName ?? info.FullName;
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant();
        return Path.Combine("/tmp", "codex-ipc-" + geteuid(), hash, "ipc.sock");
    }

    internal static async Task<Stream> OpenAsync(string pipeName, CancellationToken token)
    {
        if (OperatingSystem.IsWindows())
        {
            var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            try { await pipe.ConnectAsync(token); return pipe; }
            catch { await pipe.DisposeAsync(); throw; }
        }
        if (!OperatingSystem.IsLinux()) throw new PlatformNotSupportedException("Desktop IPC supports Windows and Linux only.");
        var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        try
        {
            await socket.ConnectAsync(new UnixDomainSocketEndPoint(LinuxSocketPath()), token);
            uint length = (uint)Marshal.SizeOf<Credentials>();
            // Linux SOL_SOCKET/SO_PEERCRED: authenticate the connected peer, not a path check before connect.
            if (getsockopt(socket.SafeHandle.DangerousGetHandle().ToInt32(), 1, 17, out var peer, ref length) != 0 ||
                length != Marshal.SizeOf<Credentials>() || peer.Uid != geteuid())
                throw new UnauthorizedAccessException("Desktop IPC peer is not the current Unix user.");
            return new NetworkStream(socket, ownsSocket: true);
        }
        catch { socket.Dispose(); throw; }
    }
}
