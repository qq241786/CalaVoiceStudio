"""软件内「下载 / 依赖」页的数据：只收录免费工具的官方下载地址。

约定：
- 每条 item = {label, url, kind, detail}
- kind="file" 是真的文件直链（点了就开始下载）；kind="page" 是发布页/网盘页面
- 只放官方或作者发布的链接；游戏本体与 Adobe Audition 不在列表里（需自行通过 Steam / Adobe 安装）
"""

from __future__ import annotations

APP_REPO = "https://github.com/qq241786/CalaVoiceStudio"
APP_RELEASES = APP_REPO + "/releases"

GAME_AND_AU_NOTE = (
    "游戏本体与 Adobe Audition 不在本列表：游戏请通过 Steam 安装，AU 请通过 Adobe 官方安装。"
)

INSTALL_ORDER_NOTE = (
    "推荐顺序：① 下载并解压 GPT-SoVITS → ② 打开本软件「设置」页点「一键检测外部工具」→ "
    "③ 按需下载打包工具 / CalabiYau → ④ 写台词、批量生成、放入 AU 或直接打包。"
)

DOWNLOAD_GROUPS = (
    {
        "title": "GPT-SoVITS（语音后端，必需）",
        "note": "版本 v2pro-20250604。解压后把「GPT-SoVITS 目录」指向含 api_v2.py 与 runtime\\python.exe 的那一层。",
        "items": (
            {
                "label": "官方整合包 · Windows 7z（HuggingFace）",
                "url": "https://huggingface.co/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v2pro-20250604.7z?download=true",
                "kind": "file",
                "detail": "官方原版，体积很大",
            },
            {
                "label": "50 系显卡特供版（HuggingFace）",
                "url": "https://huggingface.co/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v2pro-20250604-nvidia50.7z?download=true",
                "kind": "file",
                "detail": "RTX 50 系显卡专用",
            },
            {
                "label": "国内满速直链（魔搭 ModelScope）",
                "url": "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604.7z",
                "kind": "file",
                "detail": "国内推荐",
            },
            {
                "label": "国内镜像（hf-mirror）",
                "url": "https://hf-mirror.com/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v2pro-20250604.7z?download=true",
                "kind": "file",
                "detail": "HuggingFace 镜像",
            },
            {
                "label": "百度网盘（提取码 mqpi）",
                "url": "https://pan.baidu.com/s/1OE5qL0KreO-ASHwm6Zl9gA?pwd=mqpi",
                "kind": "page",
                "detail": "有会员可满速",
            },
            {
                "label": "项目主页 / 发布页",
                "url": "https://github.com/RVC-Boss/GPT-SoVITS/releases/tag/20250606v2pro",
                "kind": "page",
                "detail": "RVC-Boss/GPT-SoVITS",
            },
        ),
    },
    {
        "title": "CalaPlayerSrcmBuilder（打包工具，推荐 v1.2.1）",
        "note": "官方建议更新到 v1.2.1（修复重复打包误报、图集空位表过期会覆盖原生缩略图等问题）。",
        "items": (
            {
                "label": "发布页 v1.2.1",
                "url": "https://github.com/killa0132/CalaplayUpper/releases/tag/v1.2.1",
                "kind": "page",
                "detail": "先看说明再下载",
            },
            {
                "label": "GUI 精简版（需本机已有 FFmpeg）",
                "url": "https://github.com/killa0132/CalaplayUpper/releases/download/v1.2.1/CalaPlayerSrcmBuilder_GUI_minimal_v1.2.0.zip",
                "kind": "file",
                "detail": "约 81.5 MB",
            },
            {
                "label": "CLI 精简版",
                "url": "https://github.com/killa0132/CalaplayUpper/releases/download/v1.2.1/CalaPlayerSrcmBuilder_CLI_minimal_v1.2.1.zip",
                "kind": "file",
                "detail": "命令行批量打包用",
            },
            {
                "label": "GUI 完全体（自带 FFmpeg，普通用户推荐）",
                "url": "https://pan.baidu.com/s/1Rh14I-Jh39mFSkM0SASnwA?pwd=65yd",
                "kind": "page",
                "detail": "百度网盘，提取码 65yd",
            },
            {
                "label": "GUI 完全体备用（Google Drive）",
                "url": "https://drive.google.com/drive/folders/1tP1XLpDY28jZr6EAhaKAheNOFKRYClMN?usp=sharing",
                "kind": "page",
                "detail": "Google Drive 文件夹",
            },
            {
                "label": "项目主页",
                "url": "https://github.com/killa0132/CalaplayUpper",
                "kind": "page",
                "detail": "killa0132/CalaplayUpper",
            },
        ),
    },
    {
        "title": "CalabiYauVoice_GUI（卡拉彼丘 Wiki 语音下载 / 音频工具）",
        "note": "版本 2.0.2。软件里的「打开 CalabiYau」按钮会自动补它的默认保存/转换目录。",
        "items": (
            {
                "label": "发布页 2.0.2",
                "url": "https://github.com/znzsofficial/CalabiYauVoice_GUI/releases/tag/2.0.2",
                "kind": "page",
                "detail": "含更新说明",
            },
            {
                "label": "Windows 成品包（zip 直链）",
                "url": "https://github.com/znzsofficial/CalabiYauVoice_GUI/releases/download/2.0.2/CalabiYauVoice_GUI.zip",
                "kind": "file",
                "detail": "约 54.4 MB",
            },
            {
                "label": "项目主页",
                "url": "https://github.com/znzsofficial/CalabiYauVoice_GUI",
                "kind": "page",
                "detail": "znzsofficial/CalabiYauVoice_GUI",
            },
        ),
    },
    {
        "title": "CalaVoiceStudio（本软件）",
        "note": "当前版本 0.2.0。检查更新或分享给朋友时用这两个链接。",
        "items": (
            {
                "label": "发布页（最新成品包）",
                "url": APP_RELEASES,
                "kind": "page",
                "detail": "GitHub Releases",
            },
            {
                "label": "项目主页（源码 / 更新日志）",
                "url": APP_REPO,
                "kind": "page",
                "detail": "qq241786/CalaVoiceStudio",
            },
        ),
    },
)


def all_urls() -> list[str]:
    """返回所有链接（测试/自检用）。"""
    urls: list[str] = []
    for group in DOWNLOAD_GROUPS:
        for item in group.get("items") or ():
            url = str(item.get("url") or "").strip()
            if url:
                urls.append(url)
    return urls
