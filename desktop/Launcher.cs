using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Small visible-app launcher. No shell, registry, elevation, downloader or service.
internal static class Launcher {
    [STAThread]
    private static void Main(string[] args) {
        Application.EnableVisualStyles();
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            string app = Path.Combine(root, "app", "app.pyw");
            if (!File.Exists(python) || !File.Exists(app))
                throw new IOException("安装文件不完整。请把整个安装文件夹解压后，再双击 NovelStudio.exe；不要只复制一个 exe。");
            string role = "";
            if (args.Length != 0) {
                if (args.Length != 2 || args[0] != "--role" || (args[1] != "writer" && args[1] != "editor"))
                    throw new ArgumentException("无法识别这个启动入口，请使用作者控制中心或本程序创建的桌面快捷方式。");
                role = " --role " + args[1];
            }
            var start = new ProcessStartInfo();
            start.FileName = python;
            start.Arguments = "-I -B \"" + app + "\"" + role;
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            Process.Start(start);
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Novel Studio 无法启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
