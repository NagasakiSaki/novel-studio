using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Fixed local entry point. Never executes user command text or elevates.
internal static class WorkbenchLauncher {
    [STAThread]
    private static void Main(string[] args) {
        Application.EnableVisualStyles();
        try {
            if (args.Length != 0) throw new ArgumentException("请直接双击 Novel Studio，不需要输入启动参数。");
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            string entry = Path.Combine(root, "desktop-entry.pyw");
            if (!File.Exists(python) || !File.Exists(entry)) throw new IOException("安装文件不完整，请保留并解压整个程序文件夹。");
            Process child = Process.Start(new ProcessStartInfo {
                FileName = python, Arguments = "-I -B \"" + entry + "\"", WorkingDirectory = root,
                UseShellExecute = false, CreateNoWindow = true
            });
            // Lifetime-bound launcher only; no service, settings or user files.
            child.WaitForExit();
            if (child.ExitCode != 0) {
                MessageBox.Show("程序异常结束。退出代码：0x" + unchecked((uint)child.ExitCode).ToString("X8") +
                    "\n重新打开后，可在 设置 → 高级与安装 → 诊断日志 导出最近操作记录。请保留此退出代码。",
                    "Novel Studio 异常退出", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Novel Studio 无法启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
