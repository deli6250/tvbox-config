# 央视卫视直播源爬虫（live-crawler）

每天自动抓取上游候选接口、逐条实测校验、去重排序，生成本仓库的直播源订阅地址。

## 订阅地址

```
https://deli6250.github.io/tvbox-config/live/result.txt
```

M3U 版本（其他播放器用）：

```
https://deli6250.github.io/tvbox-config/live/result.m3u
```

## 文件

| 文件 | 作用 |
| --- | --- |
| `crawler.py` | 爬虫主程序（纯标准库，无需依赖） |
| `channels.txt` | 频道模板：央视 19 + 卫视 37，可增删 |
| `sources.txt` | 抓取种子列表，可增删任意 m3u/txt 地址 |
| `config.json` | 参数：每频道线路数、测速时长、IPv6 开关等 |
| `../.github/workflows/update-live.yml` | 每天 6:30 / 18:30 自动跑，结果提交到 `live/` |

## 常用参数（config.json）

- `keep_per_channel`：每频道保留几条线路，默认 6
- `include_ipv6`：家里宽带支持 IPv6 时改 `true`，能多拿到一批官方高清源
- `lenient`：默认 `true`，播放列表有效但测速失败的源保留在末尾兜底；要严格列表改 `false`

## 本地运行（结果更贴近你家宽带）

装 Python 3.8+，在本目录执行：

```
python crawler.py
```

结果在 `output/` 下，可手动替换仓库里的 `live/result.txt`。

## 注意

1. GitHub Actions 的测速节点在海外，排序未必和你家宽带一致，卡顿时在播放界面切线路。
2. 更新频率保持每天 1～2 次，不要调高，避免滥用 Actions 被限制。
3. 直播源仅供个人测试，请勿传播、售卖或商用。
