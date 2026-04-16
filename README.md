# LinuxDo 新帖子关键词检测并推送飞书

## 1. 项目结构

```
LinuxDoRssSendFeishu/
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── config/
│ └── config.json
├── data/
└── logs/

其中：
- `config/` 放配置文件
- `data/` 放 sqlite 数据库
- `logs/` 放日志
```


--- 

## 2. 使用方式
- 拉取当前仓库
- 修改 /Config/config.json内配置
- docker compose 启动
	- docker compose up -d --build

---  
## 3.Json配置说明
```
{
  // 飞书WebHook 地址
  "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx",
  // 检测RSS地址
  "rss_url": "https://linux.do/latest.rss",
  // 检测频率(默认30秒/次)
  "poll_interval": 30,
  // 日志清理检测平率(默认1小时检测一次)
  "log_cleanup_interval_seconds": 3600,
  // 默认清理4小时之前的数据
  "log_retention_hours": 4,
  // 数据库旧数据清理频率(默认12小时检测一次)
  "db_cleanup_interval_seconds": 43200,
  // 默认清理24小时之前的数据库内数据
  "db_retention_hours": 24,
  "keyword_monitor": {
  // 是否开启关键词检测
  "enabled": true,
  // 检测的关键词
  "keywords": ["关键词1", "关键词2"]
   }
}
```



































