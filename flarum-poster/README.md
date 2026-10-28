# Flarum 论坛发帖 Skill（独立部署包）

把"在 Flarum 论坛发帖"做成可复用的方法，含发帖流程与图床备选方案。

## 包含内容

- `SKILL.md`：发帖方法（站点识别、浏览器发帖、**API 发帖（自动化首选）**、内容规范、**批量投递脚本**、图片处理、排障）
- `skill.yaml`：skill 描述文件
- `scripts/upload_image.py`：图片上传腾讯云 COS 脚本（编辑器上传按钮不可用时的备选）
- `scripts/publish_discussion.py`：单篇发帖（API，支持指定板块 id）
- `scripts/verify_post.py`：发帖后核验（板块 / 字数 / 是否含图）
- `scripts/md_table_image.py`：**md 表格转图片**（本站 Flarum 不渲染 md 表格、也不允许原始 HTML，表格只能走图片）
- `scripts/republish_tables.py`：**已发帖表格修复**——扫全站找出残留 md 表格的帖子，转图后 PATCH 回写并核验
- `scripts/batch_publish.py`：**批量投递器**——一次跑多篇：要点截图 → 裁剪 → 图床 → 发帖 → 核验 → 写日志，自动判重
- `config.env.example`：COS 与论坛账号配置模板（只带模板，不带任何真实密钥），含 `TABLE_MODE` 开关（image / md）

## 部署步骤

1. 解压到任意目录
2. 安装依赖：`pip install cos-python-sdk-v5 requests pillow`
3. 复制配置：`cp config.env.example config.env`，按文件内注释填入 COS 信息与论坛账号
4. 传图测试：`python3 scripts/upload_image.py /path/to/图片.webp`，成功会返回永久公开 URL
5. 把 `SKILL.md` 交给 AI 助手（或放入其 custom-skills 目录），按方法发帖
6. 批量转发时：按 `SKILL.md`「批量投递脚本」节准备稿件 json，先 `--list` 判重、再 `--dry-run` 核版式、最后去掉 dry-run 正式发

## 批量投递的两个前提

- **内容不由脚本生成**：要点提炼、截图 HTML、正文 md 需事先准备好（人或 LLM 写），脚本只做机械执行。
- **截图依赖本机 Chrome**：macOS 默认路径 `/Applications/Google Chrome.app/...`，Linux 可用环境变量 `CHROME_BIN` 指定。

## 表格怎么办

Flarum 本站点（2.0.0-rc.5）不开 raw HTML，也不开 PipeTables，**md 表格会退化成一行文字**。

- 默认方案（脚本内置）：正文里的 md 表格自动渲染成紫系表格图并上传 COS，发出去就是图片。
- 若站长愿装扩展：`composer require ekumanov/flarum-ext-markdown-tables`（Flarum 2.0 专用），装完把 `config.env` 的 `TABLE_MODE` 改成 `md`，即恢复原生表格（可选中、窄屏横向滚动）。
- 修旧帖：`python scripts/republish_tables.py --scan` 看清单，再 `--ids ... --yes` 或 `--all --yes`。

## 注意

- `config.env` 含 COS 密钥与论坛登录凭据，只留本地，打包外传前务必删掉（打包脚本已自动剔除 `config.env`）
- 发帖内容规范见 `SKILL.md`"内容规范"节：身份真实、资讯附来源、不做疗效承诺、不给用药建议
