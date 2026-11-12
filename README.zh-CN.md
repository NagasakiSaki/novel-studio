# Novel Studio

Novel Studio 是面向小说作者的本地写作工作台。你可以离线建书、写草稿、维护资料和大纲，再按需要配置 AI 主笔与责编。审稿独立保存，正式章节由作者查看差异后确认。

[English](README.md) · [首次使用指南](docs/versions/beginner-workbench/user-guide/FIRST_SESSION.md) · [开发与验证](CONTRIBUTING.md)

## 在 Windows 上开始

1. 打开完整 Windows 程序文件夹中的 `NovelStudio.exe`。
2. 在「作品」页选择「阅读草稿与审稿意见」，先看离线示例。新建自己的作品时，选择一个方便备份的位置。
3. 点「写草稿」和「新建草稿」，写完点「保存草稿」。下次打开同一作品即可接着写。

本地写作无需模型账号。需要 AI 时，在作品页打开「首次 AI 协作」，分别选择主笔和责编，查看将发送的资料与接收方。使用官方服务可能计费。

## 源码候选

此仓库公开 Windows x64 版源码，当前候选为 `0.1.0-rc.2.dev.6`。Windows 安装包正在进行分发验收。开发者可按[构建与验证](CONTRIBUTING.md)构建本地副本并打开离线示例。

主体源码使用 [MIT 许可证](LICENSE)。第三方组件见[许可声明](THIRD_PARTY_NOTICES.md)，数据边界见[安全说明](SECURITY.md)。
