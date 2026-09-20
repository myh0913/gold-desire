# gold-desire 部署编排

| 文件 | 作用 |
| --- | --- |
| `docker-compose.yml` | 主编排：postgres / redis / api / worker / nginx（+ `migrate` 一次性服务，profile=tools） |
| `compose.host-build.yml` | override：宿主机 `npm run build` 后挂载 `frontend/dist`（替换镜像内构建） |
| `nginx.conf` | 站点配置（SPA 托管、`/api` 反代、SSE 透传、WS 升级、gzip、安全响应头），挂载为容器内 `/etc/nginx/conf.d/default.conf` |

镜像：`../backend/Dockerfile`（api/worker/migrate 共用）、`../frontend/Dockerfile`
（node 构建 → nginx 托管）。配置经根目录 `../.env` 注入（模板 `../.env.example`）。

**请勿在此目录裸跑 `docker compose`**——统一用 `../scripts/` 下的脚本
（deploy/start/stop/restart/upgrade/backup/restore），它们固定了
`--project-name gold-desire` 与 `--env-file ../.env` 的解析口径。

部署步骤、`.env` 全量说明、故障排查见 [../docs/deploy-ops.md](../docs/deploy-ops.md)。
