# 创业板 · 红利 比值与估值监测

手机随时可看的监控页面,监控:

| 指标 | 说明 | 数据源 |
|---|---|---|
| 创业板指/中证红利 比值 | <0.4 创业板机会区,>0.6 红利低波机会区 | 腾讯行情(创业板指)+中证指数官网(中证红利) |
| 沪深300 点位 | 大盘整体走势参考(近20年) | 中证指数官网 |
| 创业板估值百分位 | 创业板指 PE TTM 近五年分位 | 同花顺API(159915 跟踪指数口径) |
| 沪深300估值百分位 | 沪深300 PE TTM 近五年分位 | 同花顺API(510300 跟踪指数口径) |
| 红利低波估值百分位 | 中证红利低波动指数 PE TTM 近五年分位 | 同花顺API(512890 跟踪指数口径) |
| 红利低波股息率 | 近12个月,由全收益指数/价格指数推演 | 中证指数官网(红利低波 H30269/H20269) |
| 红利低波股债利差 | 股息率 − 10年期国债到期收益率 | 上述股息率 + 中国货币网(中债) |

图表支持 **近1年/3年/5年/10年/20年/全部** 时间范围切换(估值百分位数据源仅提供近5年)。

## 本地使用(最简单)

1. 双击 `update_data.bat` 更新数据(API Key 已存在本机 `fuyao_key.txt`)
2. 双击 `start_server.bat` 启动网页服务
3. 手机连同一 WiFi 打开 `http://192.168.1.9:8765/`(以本机局域网 IP 为准)

## 进阶:部署到 GitHub Pages(链接任何网络可开,自动更新)

数据每天(交易日)北京时间约 **18:35** 由 GitHub Actions 自动更新,页面本身是纯静态的,手机打开即看,无需电脑开机。

## 首次部署(约 10 分钟,只需做一次)

### 1. 获取 fuyao API Key

1. 打开 https://fuyao.aicubes.cn/ → 右上角**登录**(同花顺账号/手机号)
2. 登录后打开 https://fuyao.aicubes.cn/admin → **签发 API Key**(别名随意,如 `monitor`)
3. 复制 key(形如 `th-xxxx` 或一串字符)

### 2. 创建 GitHub 仓库并推送代码

1. 注册/登录 https://github.com
2. 点右上角 **+** → **New repository** → 名字填 `jiance`(或任意)→ **Public** → **Create repository**
3. 在电脑上本项目目录执行:

```bash
git init
git add .
git commit -m "init: 创业板红利监测"
git branch -M main
git remote add origin https://github.com/<你的用户名>/jiance.git
git push -u origin main
```

(推送时会弹出浏览器让你登录授权 GitHub,按提示操作即可)

### 3. 配置 API Key 密钥

1. 打开仓库页面 → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**:名称填 `FUYAO_API_KEY`,值粘贴你的 fuyao API Key → **Add secret**

### 4. 开启 GitHub Pages

1. 仓库 **Settings** → 左侧 **Pages**
2. **Source** 选 **Deploy from a branch** → Branch 选 `main` → 目录选 `/docs` → **Save**
3. 等 1 分钟左右,页面上方会显示网址: `https://<你的用户名>.github.io/jiance/`

### 5. 立即跑一次数据更新

1. 仓库页 → **Actions** → 左侧 **update-data** → **Run workflow** → **Run workflow**(绿色按钮)
2. 约 1 分钟后跑完,刷新手机上的网址即可看到数据

以后每个交易日 18:35 自动更新,不用再做任何操作。**手机浏览器打开网址,建议「添加到主屏幕」,就像一个小 App。**

## 本地运行(可选)

```bash
# Windows 上生成数据(需要 API Key)
set FUYAO_API_KEY=你的key
python updater.py

# 本地预览页面
python -m http.server 8000 --directory docs
# 浏览器打开 http://localhost:8000/
```

## 目录结构

```
updater.py                数据抓取+计算脚本(仅 stdlib,无依赖)
docs/index.html           手机端监控页面(ECharts 已本地化)
docs/data.json            数据文件(自动生成,勿手改)
docs/echarts.min.js       图表库
.github/workflows/update.yml  每日自动更新
```

## 说明与免责

- 估值百分位为"跟踪指数 PE TTM 五年分位",由同花顺基金指标接口提供(分别经 159915 创业板ETF、512890 红利低波ETF 的跟踪指数口径)。
- 股息率由中证指数官网的红利低波全收益指数(H20269)与价格指数(H30269)推演近12个月股息率。
- 中证红利(000922)指数日线来自中证指数官网;若官网不可用自动切到东方财富,再不可用则用招商中证红利ETF(515080)日线按历史锚定系数换算兜底。
- 10年期国债收益率为中国货币网公布的中债国债到期收益率。
- 本页面仅供个人研究参考,不构成投资建议。
