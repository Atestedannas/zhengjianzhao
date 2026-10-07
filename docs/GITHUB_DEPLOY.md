# 用 GitHub + GitHub Actions 自动部署

> 目标：本地改完代码 → `git push` → 服务器自己拉取最新代码、按需重建后端镜像、重启容器。
>
> **当前方案（2026-10 更新）**：代码由**服务器自己 `git pull`** 拉取，不再用 rsync 全量推送。
> 好处是只传差异、不断流；服务器目录同时也是一个 git 仓库，排错和回滚都更直接。

```
本地电脑                 GitHub 仓库                GitHub Actions 构建机            你的服务器
   │                        │                              │                          │
   │  git push              │                              │                          │
   ├───────────────────────>│  触发 workflow               │                          │
   │                        ├───────────────────────>│                                │
   │                        │                              │ 1. 检出代码（只为看提交信息）│
   │                        │                              │ 2. SSH 通知「该更新了」     │
   │                        │                              ├─────────────────────────>│
   │                        │                              │                          │ 3. git pull（自己拉差异）
   │                        │                              │                          │ 4. deploy.sh 按需重建/重启
   │                        │                              │                          │ 5. 健康检查
   │                        │                              │<─────────────────────────┤ 返回结果
```

> 服务器**需要能访问 GitHub**。腾讯云访问 GitHub 时快时慢，但 `git pull` 只传**提交差异**
> （几 KB ~ 几 MB），比原先 rsync 全量同步整目录稳得多。首次 `git fetch` 会慢一些（全量约 19MB）。

---

## 0. 已经替你准备好的东西

仓库根目录 = `D:\workpace\博客\zhijianzhao\photo-zhijianzhao`

| 文件 | 作用 |
| --- | --- |
| `.gitignore` | 排除密钥（`backend/.env*`）、大模型（`*.onnx`/`*.task`，单个 168~214MB，超 GitHub 100MB 上限）、`node_modules`、`__pycache__`、运行时产物 |
| `.gitattributes` | 仓库内统一 LF，`deploy.sh` 不会因为 `\r` 报错 |
| `.github/workflows/deploy.yml` | push 到 `main` 后：SSH 通知服务器 → 服务器 `git pull` → 部署 → 汇总结果 |
| `deploy/setup-server-git.sh` | **一次性**执行：把服务器部署目录初始化成 git 仓库并关联远端 |
| `deploy/git-pull-deploy.sh` | 服务器端日常脚本：`git pull --ff-only` → 有更新才跑 `deploy.sh` |
| `deploy/deploy.sh` | 服务器端脚本：预检 → 按文件指纹判断是否需要重建镜像 → 重启或重建容器 → 健康检查 |
| `deploy/rsync-exclude.txt` | 旧 rsync 方案的排除清单（已不再使用，留作本地 rsync 时的双保险参考） |
| `backend/models/.gitkeep` | 让 Git 保留这个目录（真实模型文件被忽略） |

**三个必须记住的约束**

1. 模型文件（`backend/models/` 下的 `u2net_human_seg.onnx`、`BiRefNet-….onnx`、
   `face_landmarker.task`、`face_parsing_segformer.onnx`）**不在仓库里，永远留在服务器上**。
   它们**不打进镜像**，而是由 compose 在运行时挂载（`backend/models` → `/app/models`），
   所以模型缺失表现为「功能降级/任务失败」，不会让构建失败。
   服务器上任何时候**不要执行 `git clean -fdx`**，也不要把 `/opt/.../backend/` 整个删掉重新 clone。
2. `backend/.env`（线上数据库口令、JWT 密钥）**不进仓库**，只存在于服务器。
   `setup-server-git.sh` 用 `git reset --hard` 只覆盖**已追踪**的文件，
   被 `.gitignore` 忽略的 `.env` 和模型会原样保留，可以放心执行。
3. 前端 `dist` 是**提交进仓库**的（`web-pc/dist`、`admin/dist`、`frontend/dist/build/h5`），
   所以服务器不需要装 Node —— nginx 是目录挂载，pull 完成即生效。

**部署为什么快**

后端代码（`backend/app`）、模型（`backend/models`）、模板配置（`backend/templates_config`）
都是**只读挂载**进容器的，所以：

| 改了什么 | 部署动作 | 耗时 |
| --- | --- | --- |
| Python 代码 / 模型文件 / presets.yaml | 重启容器 | 秒级 |
| compose 文件 / `.env` | 重建容器（不 build） | ~10 秒 |
| `requirements.txt` / `Dockerfile` | 重建镜像 + 重建容器 | 分钟级 |

判断依据是 `/opt/zhengjianzhao/photo-zhijianzhao/.deploy-state/` 下的文件指纹，
不是时间戳，重复执行结果一致。
前端 `dist` 是 nginx 目录挂载，pull 完就生效，连重启都不需要。

**为什么 apt 构建不再超时**

`python:3.10-slim` 后来滚动成了 Debian 13（trixie），老版 Dockerfile 用 `sed` 换腾讯云源
在它上面有时不生效，导致腾讯云服务器直连 `deb.debian.org` 拉包，每个小包 30~80 秒，
45 分钟都装不完一次依赖，直接顶到 CI 超时。
现在改成**直接覆盖写入** `sources.list`（不依赖原文件格式），并在换源后用 `grep` 强制验证——
换源失败会**立刻报错退出**，而不是拖到超时。实测完整构建约 **2.5 分钟**。

---

## 1. 在 GitHub 建仓库

（已完成，仓库地址 `https://github.com/Atestedannas/zhengjianzhao.git`，私有）

如果是新项目：打开 https://github.com/new → 名称随意 → 选 **Private** →
**不要**勾选 README / .gitignore / license → 创建。

---

## 2. 生成部署密钥（本地电脑执行，PowerShell）

这个密钥只用来让 GitHub Actions 登录你的服务器。

```powershell
# 1) 生成一对密钥（一路回车，密码留空）
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\photo_deploy" -C "github-actions-deploy" -N '""'

# 2) 打印【私钥】—— 整段（含 BEGIN/END 两行）复制，等下填到 GitHub Secret
Get-Content "$env:USERPROFILE\.ssh\photo_deploy" -Raw

# 3) 打印【公钥】—— 整行复制，等下授权到服务器
Get-Content "$env:USERPROFILE\.ssh\photo_deploy.pub"
```

把**公钥**装到服务器（1Panel → 终端，或本机 `ssh root@119.91.157.252`）：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAA....（刚才复制的公钥）' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

> 如果 1Panel 面板里开了「SSH 登录限制/仅密钥登录」，记得把公钥同时加到面板的 SSH 设置里。
> 服务器 SSH 端口不是 22 的话，第 3 步的 `SSH_PORT` 填真实端口。

验证（本地）：

```powershell
ssh -i "$env:USERPROFILE\.ssh\photo_deploy" -p 22 root@119.91.157.252 "hostname; ls /opt/zhengjianzhao/photo-zhijianzhao"
```

---

## 3. 配置 GitHub Secrets

仓库页面 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，依次添加 4 个：

| Name | Value | 说明 |
| --- | --- | --- |
| `SSH_HOST` | `119.91.157.252` | 服务器 IP |
| `SSH_USER` | `root` | 登录用户 |
| `SSH_PORT` | `22` | SSH 端口（不是 22 就填真实的） |
| `SSH_PRIVATE_KEY` | 第 2 步打印的**私钥全文** | 含 `-----BEGIN/END OPENSSH PRIVATE KEY-----` |

---

## 4. 服务器侧：让部署目录变成 git 仓库（只需一次）

服务器上要能 `git pull` 一个**私有**仓库，需要两样东西：**git 仓库本身** 和 **访问 GitHub 的凭证**。

### 4.1 配置 GitHub 访问凭证（二选一）

**方式 A：Personal Access Token（最简单）**

1. 打开 https://github.com/settings/tokens/new
2. Note 随便填（如 `server-pull`），Expiration 选长一点（如 1 年），
   勾选 **`repo`**（读取私有仓库足够），生成后**立即复制** token（页面关掉就看不到了）
3. 在服务器上执行（把 `ghp_xxx` 换成你的 token）：

```bash
git config --global credential.helper store
echo 'https://Atestedannas:ghp_xxx@github.com' > ~/.git-credentials
chmod 600 ~/.git-credentials
# 验证凭证能拉私有仓库
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: token ghp_xxx' https://api.github.com/repos/Atestedannas/zhengjianzhao
# 返回 200 就对了；401 说明 token 没权限或已过期
```

**方式 B：SSH deploy key**

```bash
# 服务器上生成密钥
ssh-keygen -t ed25519 -f ~/.ssh/github_pull -N ''
cat ~/.ssh/github_pull.pub
# 把公钥添加到 GitHub 仓库 → Settings → Deploy keys → Add（勾选只读即可）
# 配置 git 对这个仓库用这把密钥
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_pull
  IdentitiesOnly yes
EOF
# 然后把远端改成 SSH 地址（第 4.2 步里会把 URL 一起设好）
```

### 4.2 初始化部署目录

在服务器上执行（**只需一次**）：

```bash
cd /opt/zhengjianzhao/photo-zhijianzhao

# 如果走方式 B（SSH），先改远端地址；方式 A 保持 https 即可
bash deploy/setup-server-git.sh
```

脚本做了什么：
1. `git init` + 关联远端 `https://github.com/Atestedannas/zhengjianzhao.git`
2. `git fetch origin main` 拉取代码
3. `git reset --hard origin/main` 对齐工作区 —— **只覆盖已追踪文件**，
   `.env` 和 `*.onnx` 被 `.gitignore` 忽略，会原样保留
4. 检查 `.env` / 模型 / `presets.yaml` 是否还在，缺了会提示

执行完会打印当前 HEAD 和必须保留的文件清单。确认没有 `[缺失]` 就完成了。

> 之后日常部署由 `git-pull-deploy.sh` 负责（Actions 自动调用），不用再手动跑这个脚本。

---

## 5. 每次部署都做了什么

`deploy/git-pull-deploy.sh` → `deploy/deploy.sh`：

1. **git pull --ff-only**：只接受快进合并，拉取差异；HEAD 没变就跳过本次部署
2. **预检**：`backend/.env`、`backend/app/main.py`、`backend/templates_config/presets.yaml`
   在不在；并把旧布局的 `backend/u2net*.onnx` 自动搬到 `backend/models/`
3. **识别 compose 项目名**：从 `photo-backend` 容器的 label 里读 1Panel 建的那个项目名，
   避免重复创建容器
4. **判断是否需要重建镜像**：比对 `requirements.txt` + `Dockerfile` 的指纹
5. **重建镜像（仅必要时）**：代码/模型都是挂载的，只改 Python 根本不会走到这一步
6. **让新代码生效**：配置没变就 `restart`（秒级），配置变了就
   `up -d --no-deps --force-recreate`（让挂载点和 env 重新生效）
7. **健康检查**：最多等 2 分钟，`healthy` 才算成功；失败会自动打印后端日志
8. **reload nginx**：后端容器重建后内网 IP 可能变化，不 reload 会出现「后端 healthy 但 /api 全 502」

前端 `dist` 是 nginx 目录挂载，pull 完刷新页面即可（浏览器建议 `Ctrl+F5`）。

---

## 6. 日常流程

```powershell
cd D:\workpace\博客\zhijianzhao\photo-zhijianzhao
git add -A
git commit -m "feat: 改了点什么"
git push
```

然后去看 Actions。想只改文档不部署：把改动放进 `docs/` 或写成 `*.md`（workflow 里配了 `paths-ignore`）。

**只改了前端**：本地构建后把 `dist` 也一起提交（三个目录：`frontend/dist/build/h5`、
`web-pc/dist`、`admin/dist`），push 后 nginx 目录挂载直接生效，不需要重启后端。

```powershell
# 前端构建（按需）
cd frontend; npm run build:h5; cd ..\web-pc; pnpm build; cd ..\admin; pnpm build; cd ..
git add -A
git commit -m "feat: 前端更新"
git push
```

**手动在服务器上部署**（不想等 CI，或 CI 出问题时）：

```bash
cd /opt/zhengjianzhao/photo-zhijianzhao
bash deploy/git-pull-deploy.sh          # 拉代码 + 部署
# 或强制部署（代码没变也重新走一遍）
FORCE=1 bash deploy/git-pull-deploy.sh
# 或只部署不拉代码
bash deploy/deploy.sh
```

---

## 7. 回滚

服务器目录现在是 git 仓库，回滚很直接：

```bash
cd /opt/zhengjianzhao/photo-zhijianzhao

# 看历史
git log --oneline -10

# 回到上一次部署的版本（不会删 .env / 模型）
git reset --hard HEAD~1
bash deploy/deploy.sh

# 或回到指定提交
git reset --hard <commit-id>
bash deploy/deploy.sh
```

也可以从本地推一份旧代码回去（`git checkout <旧提交> && git push -f`），效果一样。

---

## 8. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| `Permission denied (publickey)`（SSH 到服务器） | 公钥没装到服务器的 `~/.ssh/authorized_keys`，或 `SSH_USER/SSH_PORT` 填错 |
| 服务器 `git pull` 报 `Authentication failed` | 私有仓库凭证没配好：看第 4.1 步；token 过期就重新生成并更新 `~/.git-credentials` |
| 服务器 `git pull` 报 `fatal: not a git repository` | 第 4.2 步的初始化没执行或目录不对：`bash deploy/setup-server-git.sh` |
| 服务器拉 GitHub 很慢/超时 | git 只传差异，通常不大；实在慢就重试，或检查服务器到 github.com 的网络 |
| `缺少 backend/models/u2net_human_seg.onnx` | 模型文件被删了/路径不对；从备份恢复，注意不要 `git clean` |
| 改了代码但接口没变化 | 代码是挂载的，看日志里是走了 `快速重启容器` 还是 `重建容器`；确认看到 `healthy`。仍不对再 `docker logs --tail 100 photo-backend` |
| 模型相关功能降级（抠图/美颜） | `backend/models/` 里有文件缺失，日志第 2 步会列出全部模型清单；补齐文件后重新部署即可，**不需要重建镜像** |
| 健康检查超时 | 直接看日志：`docker logs --tail 100 photo-backend`；多半是 `.env` 少了变量或数据库连不上 |
| docker build 卡在 apt 拉包不动 | 换源失败了：看本文「为什么 apt 构建不再超时」；手动验证 `docker run --rm python:3.10-slim cat /etc/apt/sources.list` |
| Actions 报 `dial tcp ... i/o timeout` | 服务器防火墙/安全组没放行 GitHub Actions 的出口 IP 访问 SSH 端口；或服务器 SSH 临时不可达，重试即可 |
| 想手动跑一次 | 服务器执行 `bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/git-pull-deploy.sh` |

---

## 9. 安全提醒（重要）

1. **仓库里的 `deploy/docker-compose.yml` 有明文数据库口令**（`PhotoRoot@2026!Secure`、`PhotoApp@2026!Secure`）。
   仓库是私有的问题不大，但这两个口令在公网 `3306` 上也是可用的 —— 建议尽早：
   关闭 3306 的公网映射 + 改数据库口令（改完同步更新 `backend/.env`）。
2. `backend/.env` 里的 `JWT_SECRET_KEY=local-dev-secret-key-2026` 是默认值，
   知道它的人可以伪造任意用户 token（含无限次数账号）。建议换成随机值：
   `openssl rand -hex 32`，改完重启后端，所有用户需要重新登录。
3. `WEB_LOGIN_PASSWORD=photo2026` 是 PC 端登录密码，同样建议改掉。
4. 部署私钥（`~/.ssh/photo_deploy`）只放在 GitHub Secret 和你的电脑上，
   **不要**提交进仓库（`.gitignore` 已排除 `id_ed25519*`）。
5. 第 4.1 步写在服务器 `~/.git-credentials` 里的 token 只给了 `repo` 读权限；
   不用了就在 GitHub 上 revoke 掉。
6. 私有仓库的 Actions 每月有免费额度（2000 分钟/月），一次部署约 2~3 分钟，够用。
