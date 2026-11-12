using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class PublicWindowsLauncher {
    private const string Product = "Novel Studio";

    public sealed class Archive {
        public string Url { get; set; }
        public string Sha256 { get; set; }
        public long Size { get; set; }
        public Dictionary<string, string> Files { get; set; }
    }

    [STAThread]
    private static void Main(string[] args) {
        Application.EnableVisualStyles();
        try {
            if (args.Length != 0 || !Environment.Is64BitOperatingSystem)
                throw new IOException("请在 Windows x64 上直接双击 NovelStudio.exe。");
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string entry = Path.Combine(root, "desktop-entry.pyw");
            string planPath = Path.Combine(root, "Windows-Runtime.json");
            if (!File.Exists(entry) || !File.Exists(planPath) || Hash(planPath) != RuntimePlanDigest.Value)
                throw new IOException("程序文件不完整。请从 GitHub 重新下载并解压整个 ZIP。");

            string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (String.IsNullOrEmpty(local) || !Path.IsPathRooted(local))
                throw new IOException("无法找到当前用户的本机应用数据目录。");
            string cache = Path.Combine(Path.GetFullPath(local), "NovelStudioDesktop", "Runtime");
            RejectLinkedPath(cache);
            string ready = Path.Combine(cache, RuntimePlanDigest.Value.Substring(0, 16));
            string python = Path.Combine(ready, "runtime", "pythonw.exe");
            if (!IsReady(ready, python)) {
                string notice = "首次使用需要下载约 " + RuntimePlanDigest.DownloadMiB + " MB 的运行组件。\n\n"
                    + "下载来源：python.org 与 PyPI。组件保存在当前用户目录，无需管理员权限。\n"
                    + "安装完成后可离线写作。现在下载并安装吗？";
                if (MessageBox.Show(notice, "Novel Studio 首次安装", MessageBoxButtons.YesNo,
                    MessageBoxIcon.Information, MessageBoxDefaultButton.Button1) != DialogResult.Yes) return;
                var plan = new JavaScriptSerializer().Deserialize<Archive[]>(File.ReadAllText(planPath));
                if (!ShowSetup(cache, ready, python, plan)) return;
            }
            using (var process = Process.Start(new ProcessStartInfo {
                FileName = python, Arguments = "-I -B \"" + entry + "\"", WorkingDirectory = root,
                UseShellExecute = false, CreateNoWindow = true
            })) {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new IOException("工作台异常结束。请重新打开，或在 GitHub 提交退出代码 0x"
                        + unchecked((uint)process.ExitCode).ToString("X8") + "。");
            }
        } catch (Exception error) {
            MessageBox.Show(error is IOException ? error.Message : "启动未完成。请重新解压程序后重试。",
                Product, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static bool ShowSetup(string cache, string ready, string python, Archive[] plan) {
        using (var form = new Form())
        using (var cancellation = new CancellationTokenSource()) {
            form.Text = "安装运行组件";
            form.StartPosition = FormStartPosition.CenterScreen;
            form.ClientSize = new Size(460, 142);
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MinimizeBox = form.MaximizeBox = false;
            form.AutoScaleMode = AutoScaleMode.Font;
            var label = new Label { Left = 18, Top = 17, Width = 424, Height = 30, Text = "准备安装…" };
            var bar = new ProgressBar { Left = 18, Top = 53, Width = 424, Height = 18, Maximum = plan.Length };
            var cancel = new Button { Left = 350, Top = 94, Width = 92, Text = "取消" };
            form.Controls.AddRange(new Control[] { label, bar, cancel });
            bool finished = false, success = false;
            cancel.Click += (sender, args) => { cancel.Enabled = false; label.Text = "正在取消…"; cancellation.Cancel(); };
            form.FormClosing += (sender, args) => { if (!finished) { args.Cancel = true; cancel.PerformClick(); } };
            form.Shown += async (sender, args) => {
                var progress = new Progress<int>(n => {
                    if (!cancellation.IsCancellationRequested) {
                        bar.Value = n;
                        label.Text = "正在下载和核验运行组件 " + Math.Min(n + 1, plan.Length) + " / " + plan.Length;
                    }
                });
                try {
                    await Task.Run(() => Install(cache, ready, python, plan, progress, cancellation.Token));
                    success = true;
                } catch (OperationCanceledException) {
                    if (!cancellation.IsCancellationRequested) ShowSetupFailure(form);
                } catch (Exception) {
                    ShowSetupFailure(form);
                } finally {
                    finished = true;
                    form.Close();
                }
            };
            form.ShowDialog();
            return success;
        }
    }

    private static void ShowSetupFailure(Form owner) {
        MessageBox.Show(owner, "组件安装未完成。请检查网络连接和可用磁盘空间，关闭其他安装窗口后，再次双击 NovelStudio.exe。",
            Product, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private static void Install(string cache, string ready, string python, Archive[] plan,
        IProgress<int> progress, CancellationToken token) {
        Directory.CreateDirectory(cache);
        RejectLinkedPath(cache);
        string lockPath = Path.Combine(cache, "setup.lock");
        RejectLinkedPath(lockPath);
        using (var setupLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None)) {
            if (IsReady(ready, python)) return;
            if (Directory.Exists(ready))
                throw new IOException("现有运行目录不完整。");
            // The staging name must fit anywhere the final runtime directory fits.
            string staging = Path.Combine(cache, "s-" + Guid.NewGuid().ToString("N").Substring(0, 12));
            if (Directory.Exists(staging)) throw new IOException("运行组件暂存目录已存在。");
            Directory.CreateDirectory(staging);
            try {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                using (var handler = new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
                using (var http = new HttpClient(handler)) {
                    for (int index = 0; index < plan.Length; index++) {
                        token.ThrowIfCancellationRequested();
                        progress.Report(index);
                        Fetch(http, plan[index], staging, token).GetAwaiter().GetResult();
                    }
                }
                token.ThrowIfCancellationRequested();
                File.WriteAllText(Path.Combine(staging, "runtime", "python313._pth"),
                    "python313.zip\n.\n../qt\n../parsers\n../providers\n");
                File.WriteAllText(Path.Combine(staging, "READY"), RuntimePlanDigest.Value);
                Directory.Move(staging, ready);
            } finally {
                if (Directory.Exists(staging)) {
                    RejectLinkedPath(staging);
                    Directory.Delete(staging, true);
                }
            }
        }
    }

    private static async Task Fetch(HttpClient http, Archive archive, string staging, CancellationToken token) {
        var uri = new Uri(archive.Url);
        if (uri.Scheme != "https" || (uri.Host != "www.python.org" && uri.Host != "files.pythonhosted.org"))
            throw new IOException("运行组件来源无效。");
        string download = Path.Combine(staging, "download.zip");
        using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(token)) {
            timeout.CancelAfter(TimeSpan.FromMinutes(3));
            using (var response = await http.GetAsync(uri, HttpCompletionOption.ResponseHeadersRead, timeout.Token)) {
                response.EnsureSuccessStatusCode();
                using (var input = await response.Content.ReadAsStreamAsync())
                using (var output = new FileStream(download, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    var buffer = new byte[65536];
                    long total = 0;
                    int count;
                    while ((count = await input.ReadAsync(buffer, 0, buffer.Length, timeout.Token)) != 0) {
                        total += count;
                        if (total > archive.Size) throw new IOException("运行组件大小不匹配。");
                        output.Write(buffer, 0, count);
                    }
                    if (total != archive.Size) throw new IOException("运行组件下载不完整。");
                }
            }
        }
        if (Hash(download) != archive.Sha256) throw new IOException("运行组件哈希不匹配。");
        using (var zip = ZipFile.OpenRead(download)) {
            foreach (var file in archive.Files) {
                token.ThrowIfCancellationRequested();
                var entry = zip.GetEntry(file.Value);
                if (entry == null) throw new IOException("运行组件缺少文件。");
                string target = Path.GetFullPath(Path.Combine(staging, file.Key));
                if (!target.StartsWith(staging + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("运行组件路径无效。");
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (var input = entry.Open())
                using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    input.CopyTo(output);
            }
        }
        File.Delete(download);
    }

    private static bool IsReady(string ready, string python) {
        RejectLinkedPath(python);
        string marker = Path.Combine(ready, "READY");
        RejectLinkedPath(marker);
        return File.Exists(python) && File.Exists(marker) && File.ReadAllText(marker) == RuntimePlanDigest.Value;
    }

    private static void RejectLinkedPath(string path) {
        for (string part = path; !String.IsNullOrEmpty(part); part = Path.GetDirectoryName(part)) {
            if ((File.Exists(part) || Directory.Exists(part)) &&
                (File.GetAttributes(part) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("运行组件目录包含链接。请使用当前用户的本机文件夹。");
        }
    }

    private static string Hash(string path) {
        using (var file = File.OpenRead(path))
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
    }
}
