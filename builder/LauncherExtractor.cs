using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

// Matches the extractor entry point called by the unmodified OpenGOAL launcher.
internal static class LauncherExtractor {
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits {
        public long PerProcessTime, PerJobTime;
        public uint Flags;
        public UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo {
        public uint Size;
        public string Reserved, Desktop, Title;
        public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
        public ushort Show, ReservedSize;
        public IntPtr ReservedBytes, Input, Output, Error;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfoEx { public StartupInfo Startup; public IntPtr Attributes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInfo { public IntPtr Process, Thread; public uint ProcessId, ThreadId; }
    [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, uint count, uint flags, ref UIntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, UIntPtr size, IntPtr previous, IntPtr returnedSize);
    [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] private static extern bool CreateProcess(string app, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory, ref StartupInfoEx startup, out ProcessInfo process);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] private static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr fromProcess, IntPtr source, IntPtr toProcess, out IntPtr target, uint access, bool inherit, uint options);

    private static IntPtr InheritStandardHandle(int kind) {
        IntPtr handle = GetStdHandle(kind), duplicate;
        if (handle == IntPtr.Zero || handle == new IntPtr(-1)) return IntPtr.Zero;
        if (!DuplicateHandle(GetCurrentProcess(), handle, GetCurrentProcess(), out duplicate, 0, true, 2))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return duplicate;
    }
    private static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }
    public static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, ".builder", "python", "python.exe");
            string script = Path.Combine(root, ".builder", "source", "launcher_install.py");
            if (!File.Exists(python) || !File.Exists(script)) throw new Exception("Incomplete installer package. Import the complete ZIP through OpenGOAL.");
            var arguments = new StringBuilder(Quote(python) + " -I -B -u " + Quote(script));
            foreach (string arg in args) arguments.Append(" " + Quote(arg));
            IntPtr job = IntPtr.Zero;
            IntPtr attributes = IntPtr.Zero, jobValue = IntPtr.Zero;
            bool attributesInitialized = false;
            var info = new StartupInfo();
            var child = new ProcessInfo();
            try {
                job = CreateJobObject(IntPtr.Zero, null);
                if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
                var limits = new ExtendedLimits();
                limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                UIntPtr attributeBytes = UIntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeBytes);
                attributes = Marshal.AllocHGlobal(checked((int)attributeBytes.ToUInt64()));
                if (!InitializeProcThreadAttributeList(attributes, 1, 0, ref attributeBytes))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                attributesInitialized = true;
                jobValue = Marshal.AllocHGlobal(IntPtr.Size);
                Marshal.WriteIntPtr(jobValue, job);
                // PROC_THREAD_ATTRIBUTE_JOB_LIST assigns ownership atomically
                // during creation, including a wrapper kill before ResumeThread.
                if (!UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x2000D), jobValue,
                                               new UIntPtr((uint)IntPtr.Size), IntPtr.Zero, IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                info.Size = (uint)Marshal.SizeOf(info);
                info.Flags = 0x100; // STARTF_USESTDHANDLES: preserve launcher pipes directly.
                info.Input = InheritStandardHandle(-10);
                info.Output = InheritStandardHandle(-11);
                info.Error = InheritStandardHandle(-12);
                var startup = new StartupInfoEx { Startup = info, Attributes = attributes };
                startup.Startup.Size = (uint)Marshal.SizeOf(startup);
                if (!CreateProcess(python, arguments, IntPtr.Zero, IntPtr.Zero, true,
                                   0x08080004, IntPtr.Zero, root, ref startup, out child))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                if (ResumeThread(child.Thread) == uint.MaxValue)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                if (WaitForSingleObject(child.Process, uint.MaxValue) != 0)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                uint code;
                if (!GetExitCodeProcess(child.Process, out code)) throw new Win32Exception(Marshal.GetLastWin32Error());
                return unchecked((int)code);
            } finally {
                // Includes spawn/assignment failures; never leave a suspended child.
                if (child.Process != IntPtr.Zero) {
                    TerminateProcess(child.Process, 1);
                    WaitForSingleObject(child.Process, 10000);
                }
                if (job != IntPtr.Zero) CloseHandle(job);
                if (child.Thread != IntPtr.Zero) CloseHandle(child.Thread);
                if (child.Process != IntPtr.Zero) CloseHandle(child.Process);
                foreach (IntPtr handle in new[] { info.Input, info.Output, info.Error })
                    if (handle != IntPtr.Zero) CloseHandle(handle);
                if (attributesInitialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (jobValue != IntPtr.Zero) Marshal.FreeHGlobal(jobValue);
            }
        } catch (Exception e) {
            Console.Error.WriteLine("Cinematic installer: " + e.Message); return 1;
        }
    }
}
