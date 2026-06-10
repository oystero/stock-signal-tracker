# 止跌企稳信号追踪器

A股/港股多维度技术信号追踪，综合评分与操作建议。

## 本地运行

```bash
pip install requests
python app.py
# 访问 http://localhost:8080
```

## 部署到 Render

1. Push 到 GitHub
2. 在 Render 创建 Web Service，连接此仓库
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python app.py`
