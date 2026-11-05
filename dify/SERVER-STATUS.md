# 服务器部署记录
服务器：10.69.37.213；目录：/home/jjh/echo-inn。
后端：http://127.0.0.1:18080/docs；seekdb：12881；库：echo_inn。
数据库使用现有 /home/jjh/amd-seekdb/build_release/src/observer/seekdb 编译产物，版本 1.4.0.0，源码 revision 1-634888d9b037153fa143d2c913ba4b25503e1d62。独立数据目录 seekdb-data。没有修改其他应用数据。
用户级 systemd 服务：echo-inn、echo-seekdb；已启用 linger。
检查：systemctl --user status echo-inn echo-seekdb
重启：systemctl --user restart echo-inn echo-seekdb
复验：cd /home/jjh/echo-inn && .venv/bin/python verify.py
真实运行结果：evidence/backend-results.json、evidence/negative-results.json。
本地浏览器访问：在个人电脑执行 ssh -L 18080:127.0.0.1:18080 jjh@10.69.37.213，然后打开 http://localhost:18080/docs 。请求令牌从服务器 .env 的 GAME_TOKEN 获取，禁止公开或提交 .env。
Dify 尚未安装、导入或端到端验证。Docker 代理报免费节点繁忙，raw.githubusercontent.com 与 registry-1.docker.io 解析为 169.254.1.10，无法下载所需配置和镜像。应由服务器网络管理员恢复这些官方域名的正常访问，或使用可用的 Dify 实例并使其能连接后端。
DSL 中后端地址已改成 host.docker.internal:18080；实际 Dify 容器需能访问该地址，当前后端只监听主机回环，后续需要安排专用 Docker 网络或受控反向代理。不可直接声称当前 DSL 已完成联调。
正式提交仍需：Dify 工作流真实导入运行、截图、固定 Dify 版本、重新导出 DSL，按官方私有仓库指定分支放入 dify/。当前服务器无已确认的比赛仓库，不执行猜测性提交。
