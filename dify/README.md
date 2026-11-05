# 回声旅店 · Dify × seekdb

## 作品与技术分工
玩家归还钥匙或偷看账本会改变信任度；询问秘密根据已保存状态产生不同剧情。输入“开始”可在后续运行读取旧状态。Dify Workflow 编排输入→HTTP→输出，seekdb 实际保存玩家状态与事件，并影响下一轮结果。无需模型、Embedding、Rerank、第三方插件或 PowerContext。所有剧情为原创规则生成。

## 运行
需要 Docker Desktop Linux 容器和 Docker Compose，至少为 seekdb 分配 2GB 内存。复制 .env.example 为 .env，修改两个占位密码。执行 `docker compose up -d --build`。数据库首次初始化需要时间；若调用失败，查看 `docker compose logs seekdb` 并等待数据库就绪后重试。数据库名称 test 为默认库。

在已有的自部署 Dify 中创建 Workflow，导入 echo-inn.yml，设置环境变量 GAME_TOKEN 与 .env 一致。目标 DSL 版本 0.1.5；尚未实机确认兼容的 Dify 版本，导入后检查三个节点。若 DSL 有兼容提示，可手动重建：Start 三个必填文本 player/action/request_id → HTTP POST /turn → End 输出 HTTP body。HTTP JSON 字段与输入同名，Authorization 为 Bearer 加令牌。

Dify Docker 默认 SSRF 策略可能拦截主机私网地址。应由部署管理员在测试环境允许后端地址，并确保网络可达；Linux 需配置 host.docker.internal 的 host-gateway，或将后端和 Dify 放在同一 Docker 网络并使用 api:8000。不要全局关闭 SSRF。若 API 需从其他容器访问，可将 compose 的端口映射改为适当的内网绑定。该应用只用于本地比赛演示，player 是演示标识，不是安全身份认证。

## 完整验证样例
使用全新的 player，例如验收日期加随机字符串，每轮 request_id 唯一：
1. action=开始：trust=0，turn=1。
2. action=询问秘密：拒绝透露，trust=0，turn=2。
3. action=归还钥匙：trust=1，turn=3。
4. action=询问秘密：透露“未来的我”，trust=1，turn=4。
5. 关闭工作流页面重新运行，同 player 输入开始：记住 trust=1，turn=5。
6. 重复第5步相同 request_id：返回相同结果，turn 不增加。
7. 换新 player：trust=0；输入非法动作得到422，不修改状态。
使用新的 player 可重复初始验证，无需删除已有数据。

可通过后端 /docs 发起相同请求，需填写 Authorization。正式验收须通过 Dify 完成上述步骤。在 seekdb 执行 SELECT * FROM test.echo_players 和 SELECT * FROM test.echo_events 核对持久状态和事件。保留 Dify 节点运行截图、SQL 查询截图、docker compose images 输出及日志，隐去令牌。

## 当前验证状态与提交限制
已提供完整源码、依赖、部署配置、原创初始设定、DSL 和验证步骤。尚未完成真实 Dify 导入、seekdb 联调或截图取证，不能把预期输出当作实际运行证据。提交比赛前必须补齐这些验证，固定实际使用的 Dify 版本和 seekdb 镜像 digest。latest 仅便于首次获取，不能作为最终复现版本。截图要求在指定分支/commit 开发：当前目录没有比赛仓库，需将本目录复制进官方私有仓库的 dify/，并在要求的分支提交。

接口和部署参考：https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md 。本方案使用 MySQL 协议进行结构化状态读写，不宣称进行了向量检索。
