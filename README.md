# logrotate-lite

**60秒回答："/var/log 里哪些日志在无限涨？"**

磁盘 91%？`du` 指向 `/var/log`？本工具把 `/etc/logrotate.conf` + `/etc/logrotate.d/*`
的覆盖面算清楚，与 `/var/log` 实扫对照，一次列出：

- **HIGH — 无覆盖日志**：不在任何 logrotate stanza 路径集里 → 会无限增长，迟早写满磁盘
- **MEDIUM — dead stanza**：配置指向不存在的路径（配置腐烂）
- **MEDIUM — rotate 0**：轮转即删，出事故时没有任何历史日志可查
- **MEDIUM — 无上限**：daily/weekly 却没有 size/maxsize，两次轮转之间就能吃满盘

纯 Python 标准库，零依赖，只读不改，日志内容永不离开本机。
Node.js 没有？没关系，这是 Python——一根命令跑完。

## 快速开始

```bash
chmod +x logrotate_lite.py
./logrotate_lite.py audit        # 审计本机
./logrotate_lite.py selftest     # 内置 13 项自检
```

示例输出：

```
logrotate_lite v1.0 — stanzas=4 active日志=8
[HIGH] /var/log/btmp — 无任何 stanza 覆盖 → 无限增长，磁盘迟早写满
[HIGH] /var/log/lastlog — 无任何 stanza 覆盖 → 无限增长，磁盘迟早写满
[MEDIUM] /var/log/ufw.log — dead stanza：指向不存在的路径
RESULT: 6 findings (2 HIGH), exit 1
```

**退出码即 API**：0=干净 / 1=有发现 / 2=用法错误。直接挂进 cron 或 CI：

```
0 9 * * 1  /opt/logrotate_lite.py audit --quiet || echo "logrotate drift" | mail -s sre@you
```

## 与完整版的差距

完整版 **Log Rotation Auditor** 额外包含：

| 能力 | lite | 完整版 |
|---|---|---|
| 无覆盖日志 HIGH 检出 | ✅ | ✅ |
| dead stanza / rotate 0 / 无上限 | ✅ | ✅ |
| compress 缺失 / sharedscripts 检出 | — | ✅ |
| `--allow` 豁免模式 | — | ✅ |
| `--baseline` 增量 ratchet（只报新增漂移） | — | ✅ |
| 离线 `--dir` 模式（分析别的机器的配置拷贝） | — | ✅ |
| `--json` 结构化输出 | — | ✅ |
| 自检断言 | 13 | 34 |

👉 [Log Rotation Auditor — $19](https://qiliang.gumroad.com/l/ozftp)
（一次购买，永久使用，离线运行，无订阅无遥测）

## 同类工具（同店在售）

- [Disk Space Auditor](https://qiliang.gumroad.com) — 容量现状：df 之外的第二死因（inode）
- [Cron Job Auditor](https://qiliang.gumroad.com) — 定时任务里藏了什么
- [Systemd Timer Auditor](https://qiliang.gumroad.com) — 谁在你的服务器上加了 timer

## License

MIT
