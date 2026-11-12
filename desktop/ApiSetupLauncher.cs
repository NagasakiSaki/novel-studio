using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Fixed author-facing settings only. No novel project, CLI input or elevation.
internal static class ApiSetupLauncher {
    [STAThread]
    private static void Main(string[] args) {
        Application.EnableVisualStyles();
        try {
            if (args.Length != 0) throw new ArgumentException("请直接双击 API 设置入口，无需输入参数。");
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            string entry = Path.Combine(root, "api-setup.pyw");
            if (!File.Exists(python) || !File.Exists(entry)) throw new IOException("请保留完整程序文件夹。");
            Process.Start(new ProcessStartInfo {
                FileName = python, Arguments = "-I -B \"" + entry + "\"", WorkingDirectory = root,
                UseShellExecute = false, CreateNoWindow = true
            });
        } catch (Exception) {
            MessageBox.Show("API 设置未能启动，请保留完整程序文件夹后重试。", "Novel Studio", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
