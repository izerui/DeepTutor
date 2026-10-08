# ============================================
# DeepTutor /kaoyan 子路径部署 — 完整步骤
# ============================================

# 1. 创建部署目录
mkdir -p ~/deeptutor-kaoyan && cd ~/deeptutor-kaoyan

# 2. 获取 compose 文件（任选一种）
#    从仓库下载：
curl -O https://raw.githubusercontent.com/<your-org>/DeepTutor/develop/docker-compose.kaoyan.yml
#    或从本地传：
#    scp docker-compose.kaoyan.yml user@server:~/deeptutor-kaoyan/

# 3. 启动（自动拉取最新镜像 + 首次创建 auth.json 开启多账号）
docker compose -f docker-compose.kaoyan.yml up -d --pull always

# 4. 查看启动日志，等待 "✓ Ready"
docker logs -f deeptutor-kaoyan
# 看到 Ready 后 Ctrl+C 退出

# 5. 配置 Nginx（加入以下 location 块后重载）
#
#    location /kaoyan/ {
#        proxy_pass http://127.0.0.1:3782/kaoyan/;
#        proxy_http_version 1.1;
#        proxy_set_header Upgrade $http_upgrade;
#        proxy_set_header Connection "upgrade";
#        proxy_set_header Host $host;
#        proxy_set_header X-Real-IP $remote_addr;
#        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#        proxy_set_header X-Forwarded-Proto $scheme;
#        proxy_read_timeout 3600s;
#        proxy_send_timeout 600s;
#        client_max_body_size 200M;
#    }
#
nginx -t && nginx -s reload

# 6. 访问 https://你的域名/kaoyan/
#    首次进入注册页面，创建管理员账号

# ============================================
# 日常运维
# ============================================

# 更新并重启（一条命令）
cd ~/deeptutor-kaoyan
docker compose -f docker-compose.kaoyan.yml up -d --pull always

# 查看状态
docker compose -f docker-compose.kaoyan.yml ps

# 查看日志
docker logs -f deeptutor-kaoyan

# 停止（数据保留在 ./data/ 下）
docker compose -f docker-compose.kaoyan.yml down