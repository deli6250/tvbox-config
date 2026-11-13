# TVBox 配置仓库

本仓库用于托管 TVBox 客户端使用的配置接口文件，通过 GitHub Pages 提供静态访问。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `config.json` | TVBox 主配置接口（站点、解析、分类、直播、播放器、广告过滤、DNS） |
| `live.m3u` | 直播频道列表（M3U 格式，可按需替换） |

## 接口地址

启用 GitHub Pages 后，在 TVBox「设置 — 配置地址」中填写：

```
https://<你的用户名>.github.io/<仓库名>/config.json
```

## 如何替换成自己的源

1. `config.json` 中 `sites` 数组里的每一项对应一个点播站点：
   - `type`：0=xml，1=json，3=Spider(jar)
   - `api`：站点接口地址
   - `searchable` / `quickSearch` / `filterable`：搜索、快速搜索、筛选开关（1 开 0 关）
2. `spider`：Spider 类型站点所需的爬虫 jar 地址，格式 `jar地址;md5;校验值`（无则留空）。
3. `lives`：直播源，`type` 0=M3U，1=TXT；`url` 可填本站相对路径（如 `./live.m3u`）或外链。
4. 修改后请校验 JSON 语法合法，再在 TVBox 里重新加载配置测试。

## 合规提示

仅托管自己有权使用或公开合规的配置与数据源；请勿收录未经授权的影视、直播盗播接口。
