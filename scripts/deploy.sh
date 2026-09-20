#!/usr/bin/env bash
# =============================================================================
# gold-desire 首次部署：.env 准备 → 构建镜像 → 启动 → 就绪等待 → 初始化提示
# 用法：scripts/deploy.sh（从任意 cwd 执行均可）
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

step "1/5 前置检查"
require_docker

if [ ! -f "$GD_ENV_FILE" ]; then
  cp "$GD_ENV_EXAMPLE" "$GD_ENV_FILE"
  warn "已从模板生成 ${GD_ENV_FILE}，其中含【必改】项，编辑后再继续："
  warn "  POSTGRES_PASSWORD / JWT_SECRET / AGENT_MODEL_API_KEY / 上游数据源密钥"
  warn "生成密钥示例：openssl rand -hex 32"
  err "请先编辑 .env，然后重新运行 scripts/deploy.sh"
  exit 1
fi
require_env

# 敏感项仍为模板值时给出警告（不阻断：内网试跑可继续）
if grep -q '^JWT_SECRET=change-me-in-production' "$GD_ENV_FILE"; then
  warn "JWT_SECRET 仍为默认值：任何人可伪造登录令牌，生产必须更换"
fi
if grep -q '^POSTGRES_PASSWORD=gold-desire-change-me' "$GD_ENV_FILE"; then
  warn "POSTGRES_PASSWORD 仍为默认值：数据库对宿主机网络弱口令暴露"
fi
if grep -q '^DATABASE_URL=.*gold-desire-change-me' "$GD_ENV_FILE"; then
  warn "DATABASE_URL 内嵌口令与 POSTGRES_PASSWORD 需保持一致（改了前者记得同步后者）"
fi

step "2/5 构建镜像（首次约需数分钟）"
dc build

step "3/5 启动服务（postgres/redis → api[自动迁移] → worker → nginx）"
dc up -d

step "4/5 等待就绪"
if ! wait_ready 180; then
  dc ps
  exit 1
fi

step "5/5 部署完成"
dc ps

cat <<'EOF'

后续步骤：
  1) 创建首个管理员（详见 docs/deploy-ops.md「首个管理员」），容器内执行：
       docker compose -p gold-desire exec -T api python - <<'PY'
       import asyncio
       from app.core.security import hash_password
       from app.db.session import get_session_factory
       from app.models.auth import User
       from app.repositories import Repositories
       from app.repositories.auth import UserRepository
       from app.services.role_service import RoleService

       async def main() -> None:
           async with get_session_factory()() as session:
               await RoleService(Repositories.build(session)).ensure_builtin_roles()
               if await UserRepository(session).get_by_username("admin") is None:
                   session.add(User(username="admin",
                                    password_hash=hash_password("Admin123!请立即修改"),
                                    role="admin"))
               await session.commit()

       asyncio.run(main())
       PY
     （密码需含字母与数字、≥8 位；登录后立即在「设置」中修改）
  2) 登录后在「量化配置 → 数据源」确认能力主备顺序，并填好上游密钥（.env）
  3) 日常运维：scripts/start.sh | stop.sh | restart.sh | upgrade.sh | backup.sh
EOF
ok "部署成功：http://127.0.0.1:$(env_value NGINX_HTTP_PORT 80)/（对外请走域名 + HTTPS，见 deploy-ops.md）"
