# 构建说明

需要 Windows 10/11 和 Python 3.12。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --clean 帕鲁伙伴编辑器.spec
```

生成的单文件程序位于 `dist\帕鲁伙伴编辑器.exe`。

推送到 `main` 后，`.github/workflows/release.yml` 会根据 `app.py` 的
`APP_VERSION` 自动执行相同构建并发布 GitHub Release。每次发布前必须提高版本号。

仓库禁止提交任何 `.sav` 存档。功能测试请始终使用历史存档的副本。
