# 用 GitHub + GitHub Actions 自动部署

> 目标：本地改完代码 → `git push` → 服务器自动同步代码、重建后端镜像、重启容器。
> 服务器**不需要能访问 GitHub**（腾讯云访问 GitHub 常常很慢），
> 是 GitHub 的构建机主动连到你的服务器上来推文件。

```
本地电脑                 GitHub 仓库                GitHub Actions 构建机            你的服务器
   │                        │                              │                          │
   │  git push              │                              │                          │
   ├───────────────────────>│  触发 workflow               │                          │
   │                        ├─────────────────────────────>│                          │
   │                        │                              │ 1. 检出代码               │
   │                        │                              │ 2. rsync 同步文件          │
   │                        │                              ├─────────────────────────>│ /opt/zhengjianzhao/photo-zhijianzhao
   │                        │                              │ 3. SSH 执行 deploy.sh     │
   │                        │                              ├─────────────────────────>│ 重建镜像 + 重启容器
   │                        │                              │<─────────────────────────┤ 健康检查结果
```

---

## 0. 已经替你准备好的东西

仓库根目录 = `D:\workpace\博客\zhijianzhao\photo-zhijianzhao`

| 文件 | 作用 |
| --- | --- |
| `.gitignore` | 排除密钥（`backend/.env*`）、大模型（`*.onnx`/`*.task`，单个 168~214MB，超 GitHub 100MB 上限）、`node_modules`、`__pycache__`、运行时产物 |
| `.gitattributes` | 仓库内统一 LF，`deploy.sh` 不会因为 `\r` 报错 |
| `.github/workflows/deploy.yml` | push 到 `main` 后：同步文件 → 服务器上重建 → 重启 → 汇总结果 |
| `deploy/deploy.sh` | 服务器端脚本：预检 → 重建后端镜像 → 用新镜像重建容器 → 健康检查 |
| `deploy/rsync-exclude.txt` | 双保险：即使从本地 rsync，也不会覆盖服务器上的 `.env` / 模型 |
| `backend/models/.gitkeep` | 让 Git 保留这个目录（真实模型文件被忽略） |

另外**本地仓库已经建好并完成首次提交**（367 个文件，19.3MB，工作区干净），
你已经不需要再 `git init`，直接从第 1 步开始即可。

**三个必须记住的约束**

1. `backend/u2net.onnx`、`u2net_human_seg.onnx`、`backend/models/*.onnx`、`face_landmarker.task`
   **不在仓库里，永远留在服务器上**（Dockerfile 会 `COPY u2net*.onnx`，缺了构建直接失败）。
   服务器上任何时候**不要执行 `git clean -fdx`**，也不要把 `/opt/.../backend/` 整个删掉重新 clone。
2. `backend/.env`（线上数据库口令、JWT 密钥）**不进仓库**，只存在于服务器。
   首次部署前确认服务器上这个文件还在。
3. 前端 `dist` 是**提交进仓库**的（`web-pc/dist`、`admin/dist`、`frontend/dist/build/h5`），
   所以服务器不需要装 Node —— nginx 是目录挂载，同步完成即生效。

---

## 1. 在 GitHub 建仓库

1. 打开 https://github.com/new
2. **Repository name**：`photo-zhijianzhao`（随意，后面命令里对应改）
3. 选择 **Private**（私有）
4. ⚠️ 下面的 “Add a README file / Add .gitignore / Choose a license” **全部不要勾**
   （否则远程会有一个初始提交，和本地冲突）
5. 点 `Create repository`，复制页面上的仓库地址，形如：
   `https://github.com/<你的用户名>/photo-zhijianzhao.git`

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

## 4. 首次推送（本地执行）

本地仓库**已经初始化并提交好了**：367 个文件、约 19MB，
工作区干净，`.env` / 大模型 / `node_modules` / `*.zip` 都没有被提交。
现在只差关联远程仓库：

```powershell
cd D:\workpace\博客\zhijianzhao\photo-zhijianzhao

# 把 <你的用户名> 换成你的 GitHub 用户名
git remote add origin https://github.com/<你的用户名>/photo-zhijianzhao.git

# 确认没问题再推
git remote -v
git push -u origin main
```

首次 push 会弹浏览器让你登录 GitHub 授权（或让你输入 Personal Access Token 当密码）。

推完之后：仓库页面 → `Actions` → 能看到 `Deploy to production` 正在跑。
点进去看每一步日志，正常情况下最后服务器会打印「部署完成」。

---

## 5. 每次部署都做了什么

`deploy/deploy.sh` 的 5 步（日志里能逐条看到）：

1. **预检**：`backend/.env` 和两个 u2net 模型在不在（不在就直接报错退出，不会把线上弄坏）
2. **识别 compose 项目名**：从 `photo-backend` 容器的 label 里读 1Panel 建的那个项目名，
   避免重复创建容器
3. **重建镜像**：`docker compose -p <项目名> build backend celery-worker celery-beat`
   （`requirements.txt` 的改动在这一步生效，比如这次新增的 `tzdata`）
4. **重建容器**：`up -d --no-deps`（`docker restart` 不会换镜像，必须让 compose 重建）
5. **健康检查**：最多等 2 分钟，`healthy` 才算成功；失败会自动打印后端日志

nginx 不需要重启：前端是目录挂载，文件同步完刷新页面即可（浏览器建议 `Ctrl+F5`）。

---

## 6. 日常流程

```powershell
cd D:\workpace\博客\zhijianzhao\photo-zhijianzhao
git add -A
git commit -m "feat: 每日免费次数生效"
git push
```

然后去看 Actions。想只改文档不部署：把改动放进 `docs/` 或写成 `*.md`（workflow 里配了 `paths-ignore`）。

**只改了前端**也要等后端重建（约 1~3 分钟，Docker 层有缓存）。想跳过重建可以手动跑：

```bash
# 服务器上只同步前端（不重建后端）
rsync ... # 或直接改文件
```

---

## 7. 回滚

```bash
# 服务器上（有 Git 才可以；rsync 模式下服务器不是 Git 仓库）
cd /opt/zhengjianzhao/photo-zhijianzhao

# 方式一：GitHub 上 revert 那次提交，push，等 Actions 自动回滚
# 方式二：本地 git checkout <上一个提交> 重新 push
```

因为服务器目录本身不是 Git 仓库，**推荐用「重新 push 一份旧代码」的方式回滚**，
Actions 会把旧文件同步回去再重建。

---

## 8. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| `Permission denied (publickey)` | 公钥没装到服务器的 `~/.ssh/authorized_keys`，或 `SSH_USER/SSH_PORT` 填错 |
| `rsync: command not found` | 服务器没装 rsync：`apt update && apt install -y rsync`（Debian/Ubuntu）或 `yum install -y rsync` |
| `缺少 backend/u2net.onnx` | 模型文件被删了/路径不对；从备份恢复，注意不要 `git clean` |
| 构建成功但接口没变化 | 检查 `up -d` 那步是否真的重建了容器（`docker ps --format '{{.Names}}\t{{.Image}}\t{{.CreatedAt}}'`） |
| 健康检查超时 | 直接看日志：`docker logs --tail 100 photo-backend`；多半是 `.env` 少了变量或数据库连不上 |
| Actions 报 `dial tcp ... i/o timeout` | 服务器防火墙/安全组没放行 GitHub Actions 的出口 IP 访问 SSH 端口；或者用「服务器定时 git pull」方案 |
| 想手动跑一次 | 服务器执行 `bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/deploy.sh` |

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
5. 私有仓库的 Actions 每月有免费额度（2000 分钟/月），一次部署约 1~3 分钟，够用。
