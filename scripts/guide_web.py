#!/usr/bin/env python3
"""Read-only website views and assets; robot APIs stay in mobile_guide_server."""
from html import escape

WEB_ROUTES = {
    "/": {
        "key": "home",
        "title": "工作台",
        "description": "从一个入口开始管理导览、讲解和机器人状态。"
    },
    "/initialize": {
        "key": "initialize",
        "title": "一键初始化",
        "description": "明确确认机器人已准备就绪，再启动软件模块；必要时在地图上确认真实站位与朝向。"
    },
    "/map": {
        "key": "map",
        "title": "实时地图",
        "description": "只读查看固定墙体底图、机器人的当前大致位置与实时探测障碍；不会控制运动。"
    },
    "/navigation": {
        "key": "navigation",
        "title": "点位导航",
        "description": "选择已登记导览点，以当前提速档自主规划移动；独立于 Omni 和语音讲解。"
    },
    "/assistant": {
        "key": "assistant",
        "title": "导览助手",
        "description": "文字、照片或录音输入，由 Omni 回答或生成待确认计划。"
    },
    "/agent": {
        "key": "agent",
        "title": "Agent 导览",
        "description": "以导览点位姿和展板先验为依据，由 Omni 临场构思，再确认导航与讲解任务。"
    },
    "/knowledge": {
        "key": "knowledge",
        "title": "先验与文献",
        "description": "查看点位知识来源，并上传后续讲解所需的参考文献。"
    },
    "/tasks": {
        "key": "tasks",
        "title": "任务控制",
        "description": "先预览与模拟，再由操作员确认执行。"
    },
    "/points": {
        "key": "points",
        "title": "导览点管理",
        "description": "查看已保存的展板资料、讲解和 map 坐标。"
    },
    "/points/new": {
        "key": "point_new",
        "title": "添加导览点",
        "description": "停稳、填写资料、核对讲解，最后采集当前位置保存。"
    },
    "/localization": {
        "key": "localization",
        "title": "定位与重定位",
        "description": "读取实时位姿，或在已确认的位置重新定位。"
    },
    "/system": {
        "key": "system",
        "title": "系统状态",
        "description": "检查 PC2 连接与各模块状态；不会启动运动。"
    }
}

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>__PAGE_TITLE__ · DaoLan</title><link rel="stylesheet" href="/assets/guide.css"><script src="/assets/guide.js" defer></script></head>
<body data-page="__PAGE_KEY__">
<a class="skip-link" href="#main">跳到主要内容</a>
<header class="topbar">
  <a href="/" data-nav aria-label="DaoLan 工作台"><span class="brand">DAO<span>LAN</span></span><span class="brand-caption">机器人导览 · PC2 控制台</span></a>
  <div class="header-actions"><span id="task-badge">正在读取任务</span><button id="stop" class="danger" onclick="stopRobot()">停止全部任务</button></div>
</header>
<div class="app-layout">
<aside class="sidebar"><nav aria-label="主导航"><div class="nav-label">WORKSPACE</div>
<a href="/" data-nav>工作台</a><a href="/initialize" data-nav>一键初始化</a><a href="/navigation" data-nav>点位导航</a><a href="/map" data-nav>实时地图</a><a href="/agent" data-nav>Agent 导览</a><a href="/assistant" data-nav>导览助手</a><a href="/tasks" data-nav>任务控制</a><a href="/points" data-nav>导览点管理</a><a href="/knowledge" data-nav>先验与文献</a><a href="/localization" data-nav>定位与重定位</a><a href="/system" data-nav>系统状态</a>
</nav></aside>
<main id="main"><div class="breadcrumb">DaoLan / 导览工作空间</div><h1 id="page-title" tabindex="-1">__PAGE_TITLE__</h1><p id="page-description">__PAGE_DESCRIPTION__</p><div id="status" role="status" aria-live="polite">正在连接 PC2……</div>
<section class="view" data-view="home" hidden aria-label="工作台">
  <div class="card hero"><div class="eyebrow">GUIDE WORKSPACE</div><h2>把讲解、点位和机器人操作<br>放在各自清晰的入口里。</h2><p>换电开机后先初始化软件，再核对定位与任务。进入页面不会触发动作，机器人运动仍需独立授权。</p><div class="buttons"><a id="home-initialize" class="button-link primary" href="/initialize" data-nav>一键初始化</a><a id="home-navigation" class="button-link primary" href="/navigation" data-nav>选择导览点前往</a><a id="home-live-map" class="button-link" href="/map" data-nav>查看实时地图</a><a class="button-link" href="/agent" data-nav>开始 Agent 导览</a><a class="button-link" href="/points/new" data-nav>添加导览点</a></div></div>
  <div class="stats"><div class="stat"><div class="stat-label">PC2 连接</div><div class="stat-value" id="home-connection">正在连接</div></div><div class="stat"><div class="stat-label">已保存导览点</div><div class="stat-value" id="home-point-count">—</div></div><div class="stat"><div class="stat-label">Omni 服务</div><div class="stat-value" id="home-omni">正在检查</div></div></div>
  <div class="entry-grid">
    <a class="entry" href="/navigation" data-nav><small>MOTION · POINT TO POINT</small><h3>点位导航</h3><p>选择已登记的导览点，自主规划移动；使用当前提速档，不触发讲解。</p><span>选择目的地 →</span></a>
    <a class="entry" href="/map" data-nav><small>LIVE · READ ONLY</small><h3>实时地图</h3><p>简洁 2D 墙体底图，叠加当前位置、朝向、导览点与实时探测障碍。</p><span>查看现场 →</span></a>
    <a class="entry" href="/agent" data-nav><small>AGENT · POINT TO PRESENTATION</small><h3>Agent 导览</h3><p>选择展点与主题，以先验资料动态构思，预览自主导航与讲解任务。</p><span>组织导览 →</span></a>
    <a class="entry" href="/knowledge" data-nav><small>KNOWLEDGE · REFERENCES</small><h3>先验与文献</h3><p>查看展板知识来源，为点位上传文献；审核后才进入讲解先验。</p><span>管理知识 →</span></a>
    <a class="entry" href="/assistant" data-nav><small>01 · ASSISTANT</small><h3>导览助手</h3><p>文字、照片或录音问答，直接朗读与任务规划。</p><span>开始交互 →</span></a>
    <a class="entry" href="/tasks" data-nav><small>02 · TASKS</small><h3>任务控制</h3><p>检查计划、模拟步骤、确认执行，查看当前进度。</p><span>查看任务 →</span></a>
    <a class="entry" href="/points" data-nav><small>03 · GUIDE POINTS</small><h3>导览点管理</h3><p>集中查看已保存的点位、照片与讲解内容。</p><span>浏览导览点 →</span></a>
    <a class="entry" href="/localization" data-nav><small>04 · LOCALIZATION</small><h3>定位与重定位</h3><p>读取实时位姿，在已确认位置执行一键重定位。</p><span>检查定位 →</span></a>
  </div><div class="card" style="margin-top:20px"><h2>最近任务</h2><p id="home-task-status" class="muted">正在读取……</p><a class="button-link" href="/tasks" data-nav>进入任务控制</a></div>
</section>
<section class="view" data-view="initialize" hidden aria-label="一键初始化">
  <div class="card"><h2>启动导览软件模块</h2><div class="note">此操作不会切换机器人的物理运动模式、不会使能自动行走，也不会发送导航目标。初始化过程中会发送安全禁用 / StopMove，让自动运动保持关闭；请保持现场监护。</div><label><input id="initialize-ready" type="checkbox" onchange="syncInitializationButtons()">我确认机器人双脚已落地，已由官方控制进入可运动模式，并且当前停稳</label><label for="initialize-pin">操作员 PIN（初始化与地图定位确认使用）</label><input id="initialize-pin" type="password" inputmode="numeric" autocomplete="off" placeholder="按服务端权限设置填写 PIN"><div class="buttons"><button id="initialize-start" class="primary" disabled onclick="startInitialization()">一键初始化软件（不行走）</button><button class="chip" onclick="loadInitialization()">刷新初始化状态</button></div><div id="initialize-status" role="status" aria-live="polite">尚未读取初始化状态。</div><ol id="initialize-stages"></ol><p id="initialize-position" class="hint">未成功定位时不使用 TF 零点作为当前位置。</p></div>
  <div class="card" id="initial-map-panel" hidden><h2>在地图上给出当前位置的大致范围</h2><div class="note">这是粗定位先验，不是精确坐标，也不是导航目标。请对照实际场地，在大致站位按下，再朝机器人实际面向的方向拖动箭头。后台只在圆心附近 1 米、朝向约 ±45° 的范围内匹配雷达与地图，得到最终精配位姿；无需用鼠标点到 20 cm 内。无法确认机器人在圈内就不要猜测。点击地图不会自动配准；只有确认后才提交。标准 base_link 粗定位服务须重新加载定位模块才可用，首次配准仍需实机核验。</div><p class="hint" id="initial-map-status">等待地图加载……</p><div class="buttons"><button class="chip" onclick="zoomInitialMap(2)">放大地图 ×2</button><button class="chip" onclick="zoomInitialMap(0.5)">缩小地图 ÷2</button><button class="chip" onclick="zoomInitialMap(0)">适应宽度</button><button class="chip" id="initial-map-pan" onclick="toggleInitialMapPan()">切换为拖动平移</button><button class="chip" onclick="loadInitialMap()">重新读取地图</button></div><div id="initial-map-wrap"><canvas id="initial-map" width="1" height="1" tabindex="0" aria-label="当前场地地图：选择大致站位并拖动箭头指定大致朝向；圆内半径 1 米"></canvas></div><p class="hint">可放大到 8×，切换“拖动平移”查看局部后再切回选择位姿。蓝点是已保存导览点；黄色箭头与淡橙色圈是 1 米粗定位先验，不代表已经定位；绿色箭头才是最近核验的最终位姿。方向以地图为准，不能直接用屏幕左右替代机器人朝向。配准失败不会自动扩大搜索或使能运动。</p><div id="initial-map-pose" class="note" aria-live="polite">尚未选择位置与朝向。</div><label><input id="manual-reloc-confirmed" type="checkbox" onchange="syncInitializationButtons()">我确认机器人当前真实位置在 1 米圈内，实际朝向与箭头相差约 ±45° 内，并且已停稳</label><div class="buttons"><button id="manual-reloc-start" class="primary" disabled onclick="submitManualRelocation()">在1米范围匹配定位（不行走）</button></div><div id="manual-reloc-status" role="status" aria-live="polite"></div></div>
  <p class="hint warning">地图点选重定位尚待首次实机验收，旧定位接口的朝向约定需要核验。若配准失败或位置、朝向不符，请停止并检查，不要反复猜测初值；不会自动启用导航。</p>
</section>
<section class="view" data-view="map" hidden aria-label="实时地图">
  <div class="card live-map-card"><h2>现场 2D 地图</h2><p class="hint">深灰是固定底图中的已知墙体；蓝点是导览点，绿色箭头是新鲜且定位有效的机器人位姿。橙点为雷达返回，红点为局部代价地图中的占用单元，不表示已确认的物体类别。</p><div id="live-map-status" role="status" aria-live="polite">正在等待地图……</div><div id="live-map-position" class="note">当前位置未知；不会显示未定位的 TF 零点。</div><div class="buttons"><button class="chip" onclick="zoomLiveMap(2)">放大 ×2</button><button class="chip" onclick="zoomLiveMap(0.5)">缩小 ÷2</button><button class="chip" onclick="zoomLiveMap(0)">适应地图</button><button class="chip" onclick="centerLiveRobot()">机器人居中</button><button id="live-map-follow" class="chip" onclick="toggleLiveMapFollow()">开启跟随</button><button class="chip" onclick="refreshLiveMap()">重新读取地图</button></div><div id="live-map-wrap"><canvas id="live-map" width="1" height="1" tabindex="0" aria-label="只读现场地图：拖动平移，点击不会发送目标"></canvas></div><div class="live-map-legend"><span><i class="wall"></i>已知墙体</span><span><i class="unknown"></i>未知区域</span><span><i class="point"></i>导览点</span><span><i class="robot"></i>机器人</span><span><i class="scan"></i>雷达返回</span><span><i class="obstacle"></i>局部占用</span></div><p id="live-map-sensors" class="hint">传感器状态未读取。</p><p class="hint">仅在本页可见时约每 0.5 秒读取；可放大到 16×，拖动只平移，不会导航或重定位。位置过期、网络断开或地图不匹配时立即隐藏实时叠加。</p><div class="note">这是固定底图 + 实时探测的可视化，不会永久写入地图，不等于摄像头感知、重新建图或 SLAM 优化。运动授权与安全检查仍独立执行。</div></div>
</section>
<section class="view" data-view="navigation" hidden aria-label="点位导航">
  <div class="card"><h2>选择目的地，仅移动</h2><div class="note">沿用已部署提速档：直行上限 0.60 m/s、转向上限 0.70 rad/s。上限不是恒速，机器人会根据障碍、转弯和剩余距离减速；不切换官方速度模式、不跳过安全检查。</div>
    <label for="navigation-point">已登记导览点</label><select id="navigation-point" onchange="navigationPointChanged()"><option value="">正在读取点位……</option></select><p id="navigation-target" class="hint">选择点位后显示登记位置和最终朝向。</p>
    <label><input id="navigation-ready" type="checkbox" onchange="syncNavigationButtons()">我确认机器人已落地、处于官方运动模式、吊绳已拆除且当前停稳；现场有人持遥控器监护</label>
    <div class="buttons"><button id="navigation-prepare" class="primary" disabled onclick="preparePointNavigation()">准备前往此点（仅移动）</button><a class="button-link" href="/map" data-nav>查看实时地图</a><a class="button-link" href="/initialize" data-nav>初始化 / 定位</a></div>
    <p id="navigation-policy" class="hint">正在读取网站运动权限；选择或打开页面不会运动。</p><div id="navigation-status" role="status" aria-live="polite"></div><p class="hint">下一步预览目标并输入操作员 PIN 才会执行。后台复核地图、重定位、TF、激光与本目标路径；到达或失败后禁用运动。此任务不依赖展板整理、Omni 或语音服务。首次前往其他点位仍需现场监护验收。</p>
  </div>
</section>
<section class="view" data-view="assistant" hidden aria-label="导览助手">
  <div class="card"><h2>与 Omni 交互</h2><p class="hint">可以用手机输入法语音转文字。模型生成运动计划后，只会显示预览，不会直接执行。</p>
    <label for="text">你想让机器人做什么？</label><textarea id="text" maxlength="500" placeholder="例如：请介绍一下这里，或者规划一个导览任务。"></textarea>
    <div class="attachments"><div><label for="photo">照片（可选）</label><input id="photo" type="file" accept="image/*" capture="environment"></div><div><label for="audio">录音（可选，最长 30 秒）</label><input id="audio" type="file" accept="audio/*"></div></div>
    <div class="buttons"><button id="omni" onclick="askOmni()">询问 Omni</button><button id="speak" onclick="send('/api/speak')">直接朗读文字</button></div>
    <div class="examples"><button class="chip" onclick="fill('你好，请介绍一下你自己。')">自我介绍</button><button class="chip" onclick="fill('请介绍当前参观点。')">介绍当前点</button><button class="chip" onclick="fill('请先检查系统，再规划一次往返导览，先不要执行。')">规划导览任务</button></div>
    <div id="answer" aria-live="polite"></div>
    <details><summary>其他助手入口</summary><p class="hint">原有语音助手需要独立启动；当前文本与图片功能可直接使用 Omni。</p><button class="chip" id="assistant" onclick="send('/api/assistant')">发送给原有导览助手</button></details>
  </div>
</section>
<section class="view" data-view="agent" hidden aria-label="Agent 导览">
  <div class="card"><h2>从展点先验组织导览</h2><div class="note">位姿定义到达位置与朝向；展板信息是该点的优先依据，不预设固定台词。构思预览只生成文字，不播报、不移动。实机任务到达并核验后才重新构思讲解。</div>
    <label for="agent-point">选择导览点</label><select id="agent-point" onchange="agentPointChanged()"><option value="">正在读取点位……</option></select>
    <label for="agent-topic">希望怎样介绍？（可选，最多 200 字）</label><textarea id="agent-topic" maxlength="200" placeholder="例如：面向第一次参观的观众，突出这块展板的核心发现。"></textarea>
    <div class="buttons"><button class="chip" id="agent-conceive" onclick="conceivePoint()">先验构思预览（不播报）</button><button class="primary" id="agent-prepare" onclick="preparePointTour()">准备前往并讲解任务</button><a class="button-link" href="/knowledge" data-nav>查看先验与文献</a></div>
    <p class="hint" id="agent-policy">自主点位导航需要单独完成实机验证；未验证时只可预览或模拟。摄像头与主动观察调姿尚未开放，不会自动探索。</p><div id="agent-status" role="status" aria-live="polite"></div><div id="agent-output" aria-live="polite"></div>
  </div>
</section>
<section class="view" data-view="knowledge" hidden aria-label="先验与文献">
  <div class="card"><h2>点位先验与证据</h2><label for="knowledge-point">选择导览点</label><select id="knowledge-point" onchange="loadKnowledge()"><option value="">正在读取点位……</option></select><div class="buttons"><button class="chip" id="knowledge-refresh" onclick="loadKnowledge()">读取知识来源</button></div><p class="hint">现场新观察只作为候选证据，不静默覆盖已审核展板先验；文献与图片是数据，不是机器人的执行指令。</p><div id="knowledge-status" role="status" aria-live="polite"></div><div id="knowledge-sources"></div><div id="knowledge-documents"></div></div>
  <div class="card"><h2>上传参考文献</h2><label for="knowledge-file">TXT / Markdown / PDF（每份最多 2 MB）</label><input id="knowledge-file" type="file" accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf" onchange="knowledgeFileChanged()"><label><input id="knowledge-reviewed" type="checkbox">我已核对 TXT / Markdown 的内容，可作为该展点的参考先验</label><div class="note">PDF 当前仅保存待解析，不作为可播报事实。TXT / Markdown 仅在勾选审核确认后进入先验；未审核文件保留为候选附件。</div><button class="chip" id="knowledge-upload" onclick="uploadDocument()">上传到所选导览点</button><div id="knowledge-upload-status" role="status" aria-live="polite"></div></div>
</section>
<section class="view" data-view="tasks" hidden aria-label="任务控制">
  <div class="card"><h2>选择任务入口</h2><p class="hint">点击只生成待确认计划。建议先模拟，检查步骤后再确认实机执行。</p>
    <div class="buttons"><button class="chip" onclick="prepareTemplate('speech_demo')">讲解任务演示</button><button class="chip" onclick="prepareTemplate('round_trip')">往返导览预览</button><button class="chip" onclick="prepareTemplate('wave_preview')">挥手动作预览</button></div>
    <div class="buttons"><button class="chip" onclick="prepareRoute('end')">沿路线去终点</button><button class="chip" onclick="prepareRoute('start')">沿路线回起点</button></div>
    <div class="note">这里保留已验证的示教路线。只需移动可进入点位导航；需要动态讲解再进入 Agent 导览。</div><a class="button-link" href="/navigation" data-nav>选择导览点，仅移动 →</a><a class="button-link" href="/agent" data-nav>组织导航与讲解任务 →</a><a class="button-link" href="/assistant" data-nav>让 Omni 规划其他任务 →</a>
  </div>
</section>
<section class="card" id="plan-panel" hidden aria-label="任务计划与执行状态"><h2>计划与执行状态</h2><div id="confirm" aria-live="polite"></div><div id="task" aria-live="polite">正在读取任务状态……</div><p class="hint">真实运动需操作员 PIN、安全预检和现场监护。右上角随时可停止任务；物理紧急情况请使用官方遥控急停。</p></section>
<section class="view" data-view="points" hidden aria-label="导览点管理">
  <div class="toolbar"><div><h2 id="point-count">正在读取导览点……</h2><p class="hint">展开卡片查看资料；浏览不会让机器人前往该点。</p></div><a class="button-link primary" href="/points/new" data-nav>＋ 添加导览点</a></div>
  <div class="card"><label for="point-search">搜索导览点</label><input id="point-search" type="search" placeholder="输入点位名称或讲解关键词" autocomplete="off"><div class="buttons"><button class="chip" onclick="loadPoints()">刷新列表</button></div></div>
  <div id="point-list" aria-live="polite"></div>
</section>
<section class="view" data-view="point_new" hidden aria-label="添加导览点">
  <div class="toolbar"><a class="button-link" href="/points" data-nav>← 返回导览点列表</a></div>
  <div class="card"><div class="note">用遥控器带机器人到展板位置，调整朝向并停稳。此页只记录当前位置和资料，不自动导航或播报。</div>
    <div class="form-step"><div class="step-label">STEP 01 · 点位名称</div><label for="point-name">这个位置叫什么？</label><input id="point-name" type="text" maxlength="40" placeholder="例如：入口、第一块展板"></div>
    <div class="form-step"><div class="step-label">STEP 02 · 展板资料</div><label for="point-photo">展板照片（可选）</label><input id="point-photo" type="file" accept="image/*" capture="environment" onchange="boardChanged()"><div class="buttons"><button class="chip" id="board-draft" onclick="draftBoard()">Omni 整理展板导览词</button></div><p class="hint">后台分块识别、去重汇总。生成的是草稿，仍需对照展板核对。</p>
    <label for="point-speech">展板先验摘要（最多 150 字，不是固定台词）</label><textarea id="point-speech" maxlength="150" placeholder="填写核对后的展板事实摘要；Omni 会在导览时重新组织讲解。" oninput="document.getElementById('board-reviewed').checked=false"></textarea>
    <label><input id="board-reviewed" type="checkbox">我已核对图片和先验摘要，确认没有误识别或补猜</label></div>
    <div class="form-step"><div class="step-label">STEP 03 · 到达动作</div><label for="point-gesture">讲解时的标准动作</label><select id="point-gesture"><option value="">无动作</option></select><p class="hint">这里只列出已实机验证的动作；默认无动作。</p></div>
    <div class="form-step"><div class="step-label">STEP 04 · 位置与保存</div><div id="point-pose" class="hint">保存时会重新采集当前位置与朝向；无需先保存一个叫“起点”的点。</div><div class="task-buttons"><button class="chip" id="point-locate" onclick="readCurrentPose()">读取当前位置（不运动）</button><button class="primary" id="point-add" onclick="addPoint()">定位并添加导览点</button></div><div id="point-status" role="status" aria-live="polite"></div></div>
  </div>
</section>
<section class="view" data-view="localization" hidden aria-label="定位与重定位">
  <div class="card"><h2>当前位置</h2><p class="hint">换电后先成功重定位一次；之后移动由定位器持续跟踪，不需要每到一个展板重新定位。</p><div id="local-pose" class="note">尚未采集实时位姿。点击下方按钮读取。</div><button class="chip" id="local-locate" onclick="readCurrentPose()">读取当前位置（不运动）</button><div id="local-pose-status" role="status" aria-live="polite"></div></div>
  <div class="card"><h2>一键重定位</h2><div class="note">先把机器人放到所选已确认位置附近，朝向一致并停稳。不要在未知位置套用旧起点；重定位不会启用自动运动。</div><label for="reloc-point">选择重定位初始位置</label><select id="reloc-point" onchange="showRelocSeed()"><option value="auto">自动（起点优先，否则最近确认位置）</option></select><div id="reloc-seed" class="hint">正在读取重定位初值……</div><button class="chip" id="reloc-button" onclick="relocalizeRobot()">一键重定位（不运动）</button><div id="reloc-status" role="status" aria-live="polite"></div></div>
</section>
<section class="view" data-view="system" hidden aria-label="系统状态">
  <div class="toolbar"><p class="hint">状态检查只读取现有模块，不会启动节点或解锁机器人。</p><button id="system-refresh" onclick="refreshSystem()">检查系统状态</button></div>
  <div class="card"><h2>连接与权限</h2><div class="system-grid"><div class="system-row"><span>PC2 网页连接</span><span id="system-web">尚未检查</span></div><div class="system-row"><span>Omni 服务</span><span id="system-omni">尚未检查</span></div><div class="system-row"><span>原有语音助手</span><span id="system-assistant">尚未检查</span></div><div class="system-row"><span>网页运动权限</span><span id="system-motion">尚未检查</span></div></div><p class="hint">网页运动权限不是机器人已解锁的证明，安全控制器和定位还需独立预检。</p></div>
  <div class="card"><h2>机器人模块</h2><div class="system-grid"><div class="system-row"><span>定位器</span><span id="system-localizer">尚未检查</span></div><div class="system-row"><span>导航规划器</span><span id="system-planner">尚未检查</span></div><div class="system-row"><span>安全控制器</span><span id="system-controller">尚未检查</span></div><div class="system-row"><span>重定位状态</span><span id="system-localized">尚未检查</span></div><div class="system-row"><span>地图资料</span><span id="system-map">尚未检查</span></div><div class="system-row"><span>示教路线</span><span id="system-route">尚未检查</span></div></div><p class="hint" id="system-status" role="status" aria-live="polite">点击“检查系统状态”获取最新结果。</p><details><summary>查看诊断详情</summary><pre id="system-details">尚未检查</pre></details></div>
</section>
<p class="footer-note">局域网联调控制台 · 子页面共享现有服务端口，不改变接口与机器人安全策略。</p>
</main></div></body></html>"""

CSS = r"""
:root{color-scheme:dark;font-family:Inter,system-ui,-apple-system,"Noto Sans SC",sans-serif;background:#0b1120;color:#e8edf7;--muted:#96a7bf;--line:#26344b;--surface:#131e31;--accent:#71b8ff}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;line-height:1.6}
a{color:inherit;text-decoration:none}
button,input,textarea,select{font:inherit}
button,.button-link{border:1px solid var(--line);border-radius:12px;min-height:46px;padding:10px 16px;background:#22334e;color:#eef5ff;cursor:pointer;font-weight:650;display:inline-flex;align-items:center;justify-content:center;gap:8px}
button:hover,.button-link:hover{background:#304869;border-color:#5d7da5}
button:disabled{opacity:.5;cursor:wait}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.primary,#omni{background:#397de8;border-color:#5a95ef}
#speak{background:#267f65;border-color:#3b9f80}
.danger,#stop{background:#763044;border-color:#a74660;white-space:nowrap}
.muted,.hint{color:var(--muted)}
.hint{line-height:1.7;font-size:14px;margin:10px 0 16px}
.skip-link{position:absolute;top:-80px;left:20px;padding:10px;background:#397de8;z-index:100}
.skip-link:focus{top:12px}
.topbar{position:sticky;top:0;z-index:20;min-height:78px;padding:14px 28px;background:#0b1120f2;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:18px;backdrop-filter:blur(12px)}
.brand{font-size:22px;font-weight:800;letter-spacing:1px;display:block}
.brand span{color:var(--accent)}
.brand-caption{font-size:12px;color:var(--muted)}
.header-actions{display:flex;align-items:center;gap:14px}
#task-badge{color:var(--muted);font-size:13px}
.app-layout{display:grid;grid-template-columns:220px minmax(0,1fr);max-width:1500px;margin:0 auto}
.sidebar{padding:26px 18px;border-right:1px solid var(--line);min-height:calc(100vh - 78px)}
.sidebar nav{display:flex;flex-direction:column;gap:7px;position:sticky;top:104px}
.nav-label{padding:0 12px 12px;color:#6f839e;font-size:11px;letter-spacing:2px}
.sidebar a{padding:11px 14px;border-radius:11px;color:#a8bad0;font-weight:600;font-size:14px}
.sidebar a:hover{background:#17273d;color:#fff}
.sidebar a[aria-current=page]{background:#1b3657;color:#99cbff}
main{min-width:0;padding:32px 36px 60px;max-width:1200px;width:100%}
.breadcrumb{font-size:12px;color:var(--muted);margin-bottom:8px}
h1{font-size:30px;letter-spacing:-.6px;line-height:1.25;margin:0 0 10px;outline:none}
h2{font-size:19px;margin:0 0 12px}
h3{margin:0 0 6px;font-size:18px}
p{margin:8px 0}
#page-description{color:var(--muted);margin-bottom:22px}
#status{padding:10px 14px;background:#101c2d;border:1px solid var(--line);border-radius:10px;font-size:13px;color:var(--muted);margin-bottom:24px;white-space:pre-wrap;overflow-wrap:anywhere}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:24px;margin-bottom:20px}
.hero{background:linear-gradient(120deg,#162f4d,#142237);position:relative}
.hero h2{font-size:25px;max-width:550px;margin-bottom:10px}
.hero p{max-width:620px;color:#afc3dc}
.eyebrow{font-size:11px;color:#8cbbef;letter-spacing:2px;margin-bottom:12px;font-weight:650}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}
.stat{padding:18px 20px;background:#101d30;border:1px solid var(--line);border-radius:14px}
.stat-label{font-size:12px;color:var(--muted)}
.stat-value{font-size:20px;font-weight:750;margin-top:5px}
.entry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.entry{display:block;padding:23px;border:1px solid var(--line);background:var(--surface);border-radius:16px;transition:transform .15s,border-color .15s}
.entry:hover{transform:translateY(-2px);border-color:#547ca8}
.entry small{display:block;font-size:11px;color:#7ca2cd;letter-spacing:1px;margin-bottom:8px}
.entry p{font-size:14px;color:var(--muted);margin:7px 0 16px}
.entry span{font-size:13px;color:var(--accent)}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.toolbar h2,.toolbar p{margin:0}
label{display:block;font-size:14px;font-weight:550;margin:16px 0 6px}
input[type=text],input[type=search],textarea,select{display:block;width:100%;border:1px solid #344862;border-radius:11px;background:#0a1424;color:#eef5ff;padding:12px 14px;outline:none}
input[type=password]{display:block;width:100%;border:1px solid #344862;border-radius:11px;background:#0a1424;color:#eef5ff;padding:12px 14px;outline:none}
textarea{min-height:150px;resize:vertical;font-size:16px}
input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #71b8ff17}
input[type=file]{width:100%;font-size:14px;background:#0d192b;border:1px dashed #405470;padding:12px;border-radius:10px;color:var(--muted)}
input[type=checkbox]{accent-color:#71b8ff;margin-right:7px}
.attachments{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.buttons,.task-buttons,.examples{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.chip{min-height:42px;background:#213149;font-size:14px}
.examples{margin-top:16px}
.note{border-left:3px solid #467fae;background:#102137;color:#b5c9e1;padding:12px 15px;border-radius:0 10px 10px 0;font-size:14px;margin:16px 0}
#initial-map-wrap{overflow:auto;max-height:70vh;margin-top:16px;border:1px solid var(--line);border-radius:12px;background:#e8e8e8}
#initial-map{display:block;width:100%;height:auto;max-width:none;touch-action:none;cursor:crosshair}
#initialize-status,#manual-reloc-status,#initial-map-pose{white-space:pre-wrap;overflow-wrap:anywhere}
#live-map-wrap{overflow:auto;max-height:70vh;min-height:210px;margin-top:16px;border:1px solid var(--line);border-radius:12px;background:#a0abb7;overscroll-behavior:contain}
.live-map-card .buttons{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}
.live-map-card .buttons button{min-width:0;min-height:44px;padding:10px 6px;word-break:keep-all;overflow-wrap:normal}
#live-map{display:block;width:100%;height:auto;max-width:none;touch-action:none;cursor:grab;image-rendering:pixelated}
#live-map-status,#live-map-position,#live-map-sensors{white-space:pre-wrap;overflow-wrap:anywhere}
#live-map-status{font-size:14px;margin:12px 0}
.live-map-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px;font-size:12px;color:var(--muted)}
.live-map-legend span{display:flex;align-items:center;gap:5px}.live-map-legend i{display:inline-block;width:10px;height:10px;border-radius:3px}
.live-map-legend .wall{background:#2d3745}.live-map-legend .unknown{background:#a0abb7}.live-map-legend .point{background:#126dce}.live-map-legend .robot{background:#188553}.live-map-legend .scan{background:#f69b24}.live-map-legend .obstacle{background:#dc4141}
#answer{white-space:pre-wrap;overflow-wrap:anywhere;font-size:16px;margin-top:18px;line-height:1.8}
#answer:empty{display:none}
details{margin-top:12px}
summary{cursor:pointer;color:#a7cbf4;font-size:14px;font-weight:550}
details .buttons{margin-bottom:8px}
#point-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.point-item{border:1px solid var(--line);background:var(--surface);padding:22px;border-radius:16px;min-width:0}
.point-item p{font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}
.point-item img{max-width:100%;max-height:300px;display:block;margin-top:14px;border-radius:10px}
.point-item summary{padding-top:10px}
.empty-state{color:var(--muted);padding:22px;border:1px dashed var(--line);border-radius:14px;grid-column:1/-1}
.step-label{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#8fbcec;font-weight:750;margin-top:4px}
.form-step+.form-step{margin-top:24px;padding-top:22px;border-top:1px solid var(--line)}
#point-speech{min-height:110px}
#point-status,#point-pose,#local-pose,#local-pose-status,#reloc-status,#reloc-seed{white-space:pre-wrap;overflow-wrap:anywhere}
#point-status,#local-pose-status,#reloc-status{margin-top:14px;font-size:14px}
#confirm,#task{line-height:1.8;overflow-wrap:anywhere}
#confirm:empty{display:none}
#confirm{margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid var(--line)}
#confirm ol,#task ol{padding-left:22px}
.ok{color:#72d8b2!important}
.bad{color:#ff9cad!important}
.warning{color:#ffd39a!important}
.system-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}
.system-row{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;border-bottom:1px solid var(--line);padding:13px 0;font-size:14px}
.system-row span:first-child{color:var(--muted)}
#system-details{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#0a1424;padding:16px;border-radius:10px}
.footer-note{font-size:12px;color:#6f839e;margin-top:24px}
@media(max-width:900px){.app-layout{grid-template-columns:180px minmax(0,1fr)}main{padding:26px 22px}.sidebar{padding:23px 10px}.topbar{padding:12px 20px}}
@media(max-width:640px){.topbar{padding:12px 16px;min-height:74px}.brand{font-size:20px}.brand-caption{font-size:10px}.header-actions{gap:8px}#task-badge{display:none}#stop{font-size:12px;min-height:42px;padding:9px 12px}.app-layout{display:block}.sidebar{min-height:auto;padding:10px 12px;border-right:0;border-bottom:1px solid var(--line);position:sticky;top:74px;background:#0b1120;z-index:15}.sidebar nav{flex-direction:row;overflow-x:auto;position:static;gap:5px}.sidebar a{white-space:nowrap;padding:8px 12px;font-size:13px}.nav-label{display:none}main{padding:22px 16px 48px}h1{font-size:26px}.card{padding:18px}.hero h2{font-size:22px}.stats{grid-template-columns:1fr 1fr;gap:10px}.stats .stat:last-child{grid-column:1/-1}.stat{padding:14px}.entry-grid,#point-list,.system-grid,.attachments{grid-template-columns:1fr}.entry{padding:20px}.buttons>button{flex:1}.task-buttons>button{flex:1}.toolbar .button-link{width:100%}.system-row{padding:12px 0}}
@media(prefers-reduced-motion:reduce){.entry{transition:none}.entry:hover{transform:none}}
@media(max-width:640px){.topbar{height:78px;min-height:78px}.sidebar{top:78px;padding:8px 12px}.sidebar nav{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));overflow:visible;gap:4px}.sidebar a{min-height:38px;text-align:center;font-size:12px;padding:8px 3px}}
"""

SCRIPT = r"""
const input = document.getElementById('text');
const statusBox = document.getElementById('status');
const buttons = [...document.querySelectorAll('#assistant,#speak,#omni')];
function fill(value) { input.value = value; input.focus(); }
function setStatus(text, cls='') { statusBox.textContent = text; statusBox.className = cls; }
async function send(path) {
  const text = input.value.trim();
  if (!text) { setStatus('请先输入内容。', 'bad'); return; }
  buttons.forEach(b => b.disabled = true);
  setStatus('正在发送……');
  try {
    const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
    const body = await response.json();
    if (!response.ok || body.status !== 'success') throw new Error(body.message || `HTTP ${response.status}`);
    setStatus(body.message || '已发送。', 'ok');
  } catch (error) { setStatus(`发送失败：${error.message}`, 'bad'); }
  finally { buttons.forEach(b => b.disabled = false); }
}
async function post(path, data) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok || body.status !== 'success') throw new Error(body.message || `HTTP ${response.status}`);
  return body;
}
function b64(bytes) { let s=''; for (let i=0;i<bytes.length;i+=8192) s += String.fromCharCode(...bytes.subarray(i,i+8192)); return btoa(s); }
async function jpegB64(file, maxBytes=190000, maxDimension=960) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxDimension/Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width*scale); canvas.height = Math.round(bitmap.height*scale);
  canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height); bitmap.close();
  let quality=.78, blob;
  do { blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality)); quality-=.12; } while(blob.size>maxBytes*.95 && quality>.25);
  if (blob.size>maxBytes) throw new Error('照片太大，请裁剪到展板区域后重试');
  return b64(new Uint8Array(await blob.arrayBuffer()));
}
async function pcmB64(file) {
  const context = new (window.AudioContext||window.webkitAudioContext)();
  try {
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    if (decoded.duration>30) throw new Error('录音最长 30 秒');
    const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration*16000), 16000);
    const source = offline.createBufferSource(); source.buffer=decoded; source.connect(offline.destination); source.start();
    const samples=(await offline.startRendering()).getChannelData(0), data=new DataView(new ArrayBuffer(samples.length*2));
    for(let i=0;i<samples.length;i++) data.setInt16(i*2,Math.max(-32768,Math.min(32767,Math.round(samples[i]*32767))),true);
    return b64(new Uint8Array(data.buffer));
  } finally { await context.close(); }
}
async function askOmni() {
  buttons.forEach(b=>b.disabled=true); setStatus('Omni 正在回答……');
  try {
    const data={text:input.value.trim()}, photo=document.getElementById('photo').files[0], audio=document.getElementById('audio').files[0];
    if(photo) data.image_jpeg_base64=await jpegB64(photo);
    if(audio) data.audio_pcm16_base64=await pcmB64(audio);
    const body=await post('/api/omni',data);
    document.getElementById('answer').textContent=body.text||'Omni 没有返回文字。';
    setStatus(body.message||'回答完成', 'ok');
    if(body.confirmation) showPlan(body);
  } catch(e) {setStatus(`Omni 失败：${e.message}`,'bad');}
  finally {buttons.forEach(b=>b.disabled=false);}
}
const actionLabels={check_status:'检查系统',speak:'播报',navigate_route:'路线导航',navigate_to_point:'自主导航至导览点',verify_arrival:'核验到达',present_point:'动态构思并讲解',wait:'等待',arm_gesture:'手臂动作',stop:'停止运动'};
const stateLabels={running:'执行中',pending:'待执行',succeeded:'已完成',failed:'失败',cancelled:'已取消',skipped:'已跳过',simulated:'模拟通过',interrupted:'已中断'};
function stepLabel(step) {
  const p=step.parameters||{};
  if(step.action==='speak') return '播报：'+p.text;
  if(step.action==='navigate_route') return p.destination==='start'?'沿路线回起点':'沿路线去终点';
  if(['navigate_to_point','verify_arrival','present_point'].includes(step.action)){
    const point=savedPoints.find(item=>item.id===p.point_id),name=point?point.name:p.point_id;
    return actionLabels[step.action]+'：'+name+(step.action==='present_point'&&p.topic?' · '+p.topic:'');
  }
  if(step.action==='wait') return `等待 ${p.seconds} 秒`;
  if(step.action==='arm_gesture') return '手臂动作：'+({wave:'挥手',handshake:'握手',clap:'拍手',heart:'比心',raise_hand:'举右手'}[p.gesture]||p.gesture);
  return actionLabels[step.action]||step.action;
}
function showPlan(body) {
  if(!['assistant','tasks','agent','navigation'].includes(document.body.dataset.page)) navigateTo('/tasks');
  navigationConfirmation=body.navigation_only?body.confirmation:null;
  const box=document.getElementById('confirm'); box.textContent='';
  const title=document.createElement('strong'); title.textContent='待确认：'+body.plan.title; box.append(title);
  const list=document.createElement('ol'); for(const step of body.plan.steps){const li=document.createElement('li');li.textContent=stepLabel(step);list.append(li);} box.append(list);
  for(const warning of body.warnings||[]){const p=document.createElement('div');p.className='warning';p.textContent=warning;box.append(p);}
  const controls=document.createElement('div');controls.className='task-buttons';
  for(const [label,path] of [['模拟执行','/api/plans/dry-run'],['确认执行','/api/plans/confirm']]){
    const button=document.createElement('button');button.className='chip';button.textContent=body.navigation_only&&path.endsWith('confirm')?'确认前往（仅移动）':label;
    button.onclick=async()=>{let pin='';if(path.endsWith('confirm')&&body.requires_pin){pin=prompt('请输入操作员 PIN');if(pin===null)return;}
      if(body.navigation_only&&path.endsWith('confirm')&&(!document.getElementById('navigation-ready').checked||navigationTaskRunning||navigationPreparing)){setStatus('请确认已停稳、运动模式、拆除吊绳并现场监护；不能重复提交任务','bad');return;}
      button.disabled=true;
      try{const result=await post(path,{token:body.confirmation,pin});setStatus(result.message,'ok');box.textContent='';navigationConfirmation=null;renderTask(result.task);watchTask();}
      catch(e){setStatus(e.message,'bad');}
      finally{button.disabled=false;}};
    controls.append(button);
  }
  box.append(controls);
}
async function prepareRoute(destination) {
  try {const body=await post('/api/plans/prepare',{plan:{title:destination==='start'?'回起点':'去终点',steps:[{action:'navigate_route',parameters:{destination}}]}}); showPlan(body); setStatus('路线待确认；机器人尚未运动。');}
  catch(e){setStatus(e.message,'bad');}
}
async function prepareTemplate(name) {
  try{const body=await post('/api/plans/template',{name});showPlan(body);setStatus('任务已准备，可模拟或确认执行。');}
  catch(e){setStatus(e.message,'bad');}
}
let taskTimer=null;
function renderTask(task) {
  navigationTaskRunning=!!task&&task.state==='running';syncNavigationButtons();
  document.getElementById('home-task-status').textContent=task?(stateLabels[task.state]||task.state)+' · '+task.plan.title:'暂无任务';
  document.getElementById('task-badge').textContent=task&&task.state==='running'?'任务执行中':'无执行中任务';
  const box=document.getElementById('task');box.textContent='';if(!task)return;
  const title=document.createElement('div');title.textContent=(task.dry_run?'模拟任务：':'任务：')+task.plan.title+' · '+(stateLabels[task.state]||task.state);box.append(title);
  const list=document.createElement('ol');task.plan.steps.forEach((step,i)=>{const li=document.createElement('li');li.textContent=stepLabel(step)+' — '+(stateLabels[task.steps[i].state]||task.steps[i].state);const result=task.steps[i].result;if(step.action==='navigate_to_point'&&task.steps[i].state==='succeeded'&&result&&result.verified===true&&result.motion_disabled===true&&Number.isFinite(result.position_error_m)&&Number.isFinite(result.yaw_error_rad)){const detail=document.createElement('div');detail.className='hint';detail.textContent=`到点已核验 · 位置误差 ${(result.position_error_m*100).toFixed(1)} cm · 朝向误差 ${(result.yaw_error_rad*180/Math.PI).toFixed(1)}° · 运动已禁用`;li.append(detail);}list.append(li);});box.append(list);
  const message=document.createElement('div');message.textContent=task.message;box.append(message);
}
async function watchTask() {
  clearTimeout(taskTimer);
  try{const body=await (await fetch('/api/tasks/current')).json();renderTask(body.task);
    if(body.task&&body.task.state==='running')taskTimer=setTimeout(watchTask,1000);}
  catch(e){setStatus('任务状态连接失败，请刷新页面。','bad');}
}
async function stopRobot() {
  try {const body=await post('/api/stop',{}); setStatus(body.message,'ok'); document.getElementById('confirm').textContent='';}
  catch(e){setStatus(e.message,'bad');}
  watchTask();
  loadInitialization();loadRelocation();
}
let pointPinRequired=false, boardBusy=false, pointPoseBusy=false;
function pointStatus(text, cls='') { for(const id of ['point-status','local-pose-status']){const box=document.getElementById(id);box.textContent=text;box.className=cls;} }
function syncPointButtons() { for(const id of ['point-locate','point-add','board-draft','local-locate'])document.getElementById(id).disabled=boardBusy||pointPoseBusy; }
function showCurrentPose(body) {
  const pose=body.pose;
  document.getElementById('point-pose').textContent=`定位有效 · map 坐标：x=${pose.x.toFixed(2)} m，y=${pose.y.toFixed(2)} m，朝向 ${(pose.yaw*180/Math.PI).toFixed(1)}°`+(typeof body.tf_age_seconds==='number'?` · TF 数据年龄 ${body.tf_age_seconds.toFixed(2)} 秒`:' · 已保存');
  document.getElementById('local-pose').textContent=document.getElementById('point-pose').textContent;
}
async function readCurrentPose() {
  if(boardBusy||pointPoseBusy)return;
  pointPoseBusy=true;syncPointButtons();pointStatus('正在读取实时位置与朝向，请保持机器人停稳……');
  try{
    const response=await fetch('/api/points/pose'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'读取失败');
    showCurrentPose(body);pointStatus('当前位置已读取。确认位置正确后可添加导览点；保存时会重新采集。','ok');
  }catch(e){document.getElementById('point-pose').textContent='当前定位不可用；请勿使用旧预览坐标。';document.getElementById('local-pose').textContent='当前定位不可用；请勿使用旧预览坐标。';pointStatus('读取位置失败：'+e.message,'bad');}
  finally{pointPoseBusy=false;syncPointButtons();}
}
function boardChanged() { document.getElementById('board-reviewed').checked=false; pointStatus('照片已更换，请重新整理或手动核对导览词。'); }
let savedPoints=[];
function renderSavedPoints(){
  const box=document.getElementById('point-list'),query=document.getElementById('point-search').value.trim().toLowerCase();
  box.textContent='';
  const points=savedPoints.filter(point=>point.name.toLowerCase().includes(query)||(point.speech||'').toLowerCase().includes(query));
  document.getElementById('point-count').textContent=query?`找到 ${points.length} / ${savedPoints.length} 个导览点`:`已保存 ${savedPoints.length} 个导览点`;
  if(!points.length){const empty=document.createElement('p');empty.className='empty-state';empty.textContent=query?'没有匹配的导览点。':'还没有导览点，点击右上角“添加导览点”开始。';box.append(empty);return;}
  for(const point of points){
    const article=document.createElement('article');article.className='point-item';
    const title=document.createElement('h3');title.textContent=point.name;article.append(title);
    const summary=document.createElement('p');summary.className='muted';summary.textContent=(point.image?'展板已保存':'未附照片')+' · '+(point.speech?'先验摘要已填写':'尚未填写先验摘要');article.append(summary);
    const details=document.createElement('details'),toggle=document.createElement('summary');toggle.textContent='查看先验摘要、照片与位姿';details.append(toggle);
    const speech=document.createElement('p');speech.textContent=point.speech||'尚未填写先验摘要';details.append(speech);
    const pose=document.createElement('p');pose.className='muted';pose.textContent=`map 坐标：x=${point.pose.x.toFixed(2)} m，y=${point.pose.y.toFixed(2)} m，朝向=${(point.pose.yaw*180/Math.PI).toFixed(1)}°`;details.append(pose);
    if(point.gesture){const gesture=document.createElement('p');gesture.textContent='到达后动作：'+({wave:'挥手',handshake:'握手',clap:'拍手',heart:'比心',raise_hand:'举右手'}[point.gesture]||point.gesture);details.append(gesture);}
    if(point.image){const img=document.createElement('img');img.src=point.image;img.alt=point.name+'展板照片';img.loading='lazy';details.append(img);}
    article.append(details);
    const controls=document.createElement('div');controls.className='buttons';
    const move=document.createElement('button');move.className='chip';move.textContent='前往此点（仅移动）';move.onclick=()=>selectNavigationPoint(point.id);controls.append(move);
    const agent=document.createElement('button');agent.className='chip';agent.textContent='以此点组织导览';agent.onclick=()=>selectAgentPoint(point.id);controls.append(agent);
    const knowledge=document.createElement('button');knowledge.className='chip';knowledge.textContent='先验与文献';knowledge.onclick=()=>{document.getElementById('knowledge-point').value=point.id;navigateTo('/knowledge');};controls.append(knowledge);
    article.append(controls);box.append(article);
  }
}
async function loadPoints(){
  try{
    const response=await fetch('/api/points'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'读取失败');
    pointPinRequired=body.requires_pin;savedPoints=body.points;
    document.getElementById('home-point-count').textContent=String(savedPoints.length);
    renderSavedPoints();syncPointSelects();navigationPointChanged(false);if(document.body.dataset.page==='knowledge')loadKnowledge();await loadRelocation(true);
  }catch(e){document.getElementById('point-count').textContent='点位读取失败';pointStatus('读取导览点失败：'+e.message,'bad');}
}
function syncPointSelects(){
  for(const id of ['agent-point','knowledge-point','navigation-point']){
    const select=document.getElementById(id),previous=select.value;select.textContent='';
    if(!savedPoints.length){const option=document.createElement('option');option.value='';option.textContent='暂无已保存点位，请先添加导览点';select.append(option);select.value='';continue;}
    for(const point of savedPoints){const option=document.createElement('option');option.value=point.id;option.textContent=point.name;select.append(option);}
    select.value=savedPoints.some(point=>point.id===previous)?previous:savedPoints[0].id;
  }
}
let navigationPreparing=false,navigationTaskRunning=false,navigationConfirmation=null;
function syncNavigationButtons(){document.getElementById('navigation-prepare').disabled=navigationPreparing||navigationTaskRunning||!document.getElementById('navigation-ready').checked||!document.getElementById('navigation-point').value;}
function navigationPointChanged(clearConfirmation=true){
  if(clearConfirmation){document.getElementById('navigation-ready').checked=false;if(navigationConfirmation){document.getElementById('confirm').textContent='';navigationConfirmation=null;}}
  const point=savedPoints.find(item=>item.id===document.getElementById('navigation-point').value);
  document.getElementById('navigation-target').textContent=point?`${point.name} · map (${point.pose.x.toFixed(2)}, ${point.pose.y.toFixed(2)}) m · 最终朝向 ${(point.pose.yaw*180/Math.PI).toFixed(1)}°`:'暂无已登记点位，请先添加导览点';
  syncNavigationButtons();
}
function selectNavigationPoint(point_id){document.getElementById('navigation-point').value=point_id;navigationPointChanged();navigateTo('/navigation');}
async function preparePointNavigation(){
  if(navigationPreparing||navigationTaskRunning)return;
  const point_id=document.getElementById('navigation-point').value,box=document.getElementById('navigation-status');
  if(!point_id||!document.getElementById('navigation-ready').checked){box.textContent='请选择已登记点位并确认机器人状态与现场监护';return;}
  navigationPreparing=true;syncNavigationButtons();box.textContent='正在准备移动计划；尚未发送导航目标……';
  try{const body=await post('/api/navigation/prepare',{point_id});if(document.getElementById('navigation-point').value!==point_id||!document.getElementById('navigation-ready').checked){box.textContent='选择或现场确认已改变；请重新准备，不执行旧计划';return;}showPlan(body);box.textContent=body.message;setStatus('点位移动待确认；不会调用 Omni 或播报。');}
  catch(e){box.textContent='准备失败：'+e.message;}
  finally{navigationPreparing=false;syncNavigationButtons();}
}
async function loadNavigationPolicy(){
  const box=document.getElementById('navigation-policy');
  try{const responses=await Promise.all([fetch('/api/health'),fetch('/api/actions')]),[health,actions]=await Promise.all(responses.map(r=>r.json()));if(responses.some(r=>!r.ok)||health.status!=='success'||actions.status!=='success')throw new Error('权限读取失败');box.textContent=health.motion_enabled&&actions.named_navigation_verified?'网站已开放点位导航；实机执行仍需 PIN、实时预检与现场监护。':'网站点位运动权限尚未开放；只可预览或模拟，不会使能运动。';}
  catch(e){box.textContent='未能确认网站运动权限；不要执行，请检查 PC2 连接。';}
}
function selectAgentPoint(point_id){
  document.getElementById('agent-point').value=point_id;
  agentPointChanged();
  navigateTo('/agent');
}
function agentPointChanged(){
  document.getElementById('knowledge-point').value=document.getElementById('agent-point').value;
  document.getElementById('agent-output').textContent='';
  document.getElementById('agent-status').textContent='已选择该点；点击构思预览或准备任务，不会自动运动。';
}
let agentBusy=false;
function agentRequest(){
  const point_id=document.getElementById('agent-point').value,topic=document.getElementById('agent-topic').value.trim();
  if(!savedPoints.some(point=>point.id===point_id))throw new Error('请先选择一个已保存的导览点');
  if(topic.length>200)throw new Error('讲解主题最多 200 字');
  return {point_id,topic};
}
function syncAgentButtons(){for(const id of ['agent-conceive','agent-prepare','agent-point','agent-topic'])document.getElementById(id).disabled=agentBusy;}
function renderConception(result){
  const box=document.getElementById('agent-output');box.textContent='';
  const heading=document.createElement('h3');heading.textContent=result.needs_review?'构思预览 · 尚需核对':'构思预览 · 未播报';box.append(heading);
  for(const text of result.segments||[]){const paragraph=document.createElement('p');paragraph.textContent=String(text);box.append(paragraph);}
  const sources=document.createElement('p');sources.className='hint';sources.textContent='证据来源：'+(result.source_ids||[]).join('；');box.append(sources);
  if(result.uncertainties&&result.uncertainties.length){const warnings=document.createElement('p');warnings.className='warning';warnings.textContent='需核对：'+result.uncertainties.join('；');box.append(warnings);}
  const review=document.createElement('p');review.className='hint';review.textContent='needs_review: '+String(result.needs_review)+' · 这是先验内容预览，不证明机器人已经到达该点。实机到达后会重新构思。';box.append(review);
}
async function conceivePoint(){
  if(agentBusy)return;
  const status=document.getElementById('agent-status');
  try{
    const data=agentRequest();agentBusy=true;syncAgentButtons();status.textContent='Omni 正在依据所选展点先验构思；不播报、不移动……';
    const body=await post('/api/agent/conceive',data);renderConception(body.conception||body.result||body);
    status.textContent=body.message||'构思预览完成，未调用机器人运动或播报。';
  }catch(e){status.textContent='构思失败：'+e.message;}
  finally{agentBusy=false;syncAgentButtons();}
}
async function preparePointTour(){
  if(agentBusy)return;
  const status=document.getElementById('agent-status');
  try{
    const {point_id,topic}=agentRequest();agentBusy=true;syncAgentButtons();status.textContent='正在准备点位导览计划，尚未执行……';
    const body=await post('/api/agent/prepare',{point_ids:[point_id],topic});showPlan(body);
    status.textContent='计划已准备。可先模拟；真实运动仍需操作员确认、PIN 与实机验证。';
  }catch(e){status.textContent='任务准备失败：'+e.message;}
  finally{agentBusy=false;syncAgentButtons();}
}
let knowledgeSequence=0, documentBusy=false;
async function loadKnowledge(){
  const point_id=document.getElementById('knowledge-point').value,sequence=++knowledgeSequence;
  const status=document.getElementById('knowledge-status');
  if(!savedPoints.some(point=>point.id===point_id)){status.textContent='请先选择一个已保存的导览点。';return;}
  document.getElementById('knowledge-refresh').disabled=true;status.textContent='正在读取该点的知识来源……';
  document.getElementById('knowledge-sources').textContent='';document.getElementById('knowledge-documents').textContent='';
  try{
    const response=await fetch('/api/points/'+encodeURIComponent(point_id)+'/knowledge'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'知识读取失败');
    if(sequence!==knowledgeSequence||document.getElementById('knowledge-point').value!==point_id)return;
    renderKnowledge(body.knowledge||body.context||body);status.textContent='已读取。只有审核后的资料可进入该展点先验。';
  }catch(e){if(sequence===knowledgeSequence)status.textContent='读取失败：'+e.message;}
  finally{if(sequence===knowledgeSequence)document.getElementById('knowledge-refresh').disabled=false;}
}
function renderKnowledge(knowledge){
  const sources=document.getElementById('knowledge-sources'),documents=document.getElementById('knowledge-documents');sources.textContent='';documents.textContent='';
  const heading=document.createElement('h3');heading.textContent='知识来源';sources.append(heading);
  const list=document.createElement('ul');
  for(const source of knowledge.sources||[]){const item=document.createElement('li');item.textContent=(source.source_id||source.id||'未命名来源')+' · '+(source.type||'附件')+' · '+(source.reviewed?'已审核':source.status==='stale_photo_not_used'?'旧照片版本，不用于事实':'仅参考或待审核');list.append(item);}
  if(!list.children.length){const item=document.createElement('li');item.textContent='暂无登记的知识来源。';list.append(item);}sources.append(list);
  const title=document.createElement('h3');title.textContent='文献附件';documents.append(title);
  for(const doc of knowledge.documents||[]){const item=document.createElement('p');item.textContent=doc.filename+' · '+(doc.size_bytes||0)+' bytes · '+(doc.status==='text_available'?(doc.reviewed?'已审核文本，可作为先验':'未审核文本，不作为先验'):'待解析，仅保存附件')+'\n来源 ID：'+(doc.source_id||doc.id);documents.append(item);}
  if(!(knowledge.documents||[]).length){const empty=document.createElement('p');empty.className='hint';empty.textContent='尚未上传文献，可在下方保留或补充参考资料。';documents.append(empty);}
}
function knowledgeFileChanged(){
  const file=document.getElementById('knowledge-file').files[0],reviewed=document.getElementById('knowledge-reviewed');reviewed.checked=false;reviewed.disabled=!!file&&/\.pdf$/i.test(file.name);
}
async function uploadDocument(){
  if(documentBusy)return;
  const status=document.getElementById('knowledge-upload-status'),point_id=document.getElementById('knowledge-point').value,file=document.getElementById('knowledge-file').files[0];
  try{
    if(!savedPoints.some(point=>point.id===point_id))throw new Error('请选择一个已保存的导览点');
    if(!file||! /\.(txt|md|pdf)$/i.test(file.name))throw new Error('请选择 TXT、Markdown 或 PDF 文件');
    if(file.size<=0||file.size>2*1024*1024)throw new Error('每份文献必须非空且不超过 2 MB');
    let pin='';if(pointPinRequired){pin=prompt('请输入操作员 PIN，以上传文献');if(pin===null)return;}
    documentBusy=true;for(const id of ['knowledge-upload','knowledge-file','knowledge-reviewed','knowledge-point'])document.getElementById(id).disabled=true;
    status.textContent='正在上传文献；不会播报或移动……';
    const file_base64=b64(new Uint8Array(await file.arrayBuffer()));
    const reviewed=!/\.pdf$/i.test(file.name)&&document.getElementById('knowledge-reviewed').checked;
    const body=await post('/api/points/'+encodeURIComponent(point_id)+'/documents',{filename:file.name,file_base64,reviewed,pin});
    status.textContent=body.message||(/\.pdf$/i.test(file.name)?'PDF 已保存待解析，不作为讲解事实。':'文献已保存；仅审核后的文本进入先验。');
    document.getElementById('knowledge-file').value='';document.getElementById('knowledge-reviewed').checked=false;await loadKnowledge();
  }catch(e){status.textContent='上传失败：'+e.message;}
  finally{documentBusy=false;for(const id of ['knowledge-upload','knowledge-file','knowledge-point'])document.getElementById(id).disabled=false;knowledgeFileChanged();}
}
async function draftBoard() {
  const file=document.getElementById('point-photo').files[0];
  if(!file){pointStatus('请先上传展板照片。','bad');return;}
  if(boardBusy||pointPoseBusy)return;
  boardBusy=true;syncPointButtons();
    document.getElementById('board-reviewed').checked=false;pointStatus('后台正在切分展板图片；不会播报或移动……');
  try {
    const image=await jpegB64(file,1000000,2200);
    const started=await post('/api/points/draft',{image_jpeg_base64:image});
    let job=started.job;
    while(job.state==='running'){
      pointStatus(job.message);
      await new Promise(resolve=>setTimeout(resolve,1000));
      const response=await fetch('/api/points/draft/'+started.job.id),body=await response.json();
      if(!response.ok||body.status!=='success')throw new Error(body.message||'无法读取展板处理进度');
      job=body.job;
    }
    if(job.state!=='succeeded')throw new Error(job.message||'展板整理未完成');
    if(document.getElementById('point-photo').files[0]!==file)throw new Error('照片已更换，请对新照片重新整理');
    document.getElementById('point-speech').value=job.draft.speech;
    const warnings=job.draft.uncertainties.join('；');
    pointStatus(`已处理 ${job.total} 张概览/切片，草稿已生成，请核对并编辑，再勾选确认。`+(warnings?'\n需核对：'+warnings:job.draft.needs_review?'\n存在不确定内容，请对照原展板仔细核对。':''),job.draft.needs_review?'warning':'ok');
  }catch(e){pointStatus('整理失败：'+e.message,'bad');}
  finally{boardBusy=false;syncPointButtons();}
}
async function addPoint() {
  if(boardBusy||pointPoseBusy)return;
  const data={name:document.getElementById('point-name').value.trim(),speech:document.getElementById('point-speech').value.trim(),gesture:document.getElementById('point-gesture').value};
  if(!data.name){pointStatus('请填写导览点名称。','bad');return;}
  const file=document.getElementById('point-photo').files[0];
  if(file&&!document.getElementById('board-reviewed').checked){pointStatus('请先核对展板图片与导览词，并勾选确认。','bad');return;}
  if(pointPinRequired){const pin=prompt('请输入操作员 PIN，以保存导览点');if(pin===null)return;data.pin=pin;}
  pointPoseBusy=true;syncPointButtons();
  pointStatus('正在采集当前位置与朝向，请保持机器人停稳……');
  try {
    if(file){data.image_jpeg_base64=await jpegB64(file,1000000,2200);data.board_reviewed=true;}
    const body=await post('/api/points',data);
    showCurrentPose({pose:body.point.pose});
    pointStatus(body.message,'ok');
    document.getElementById('point-name').value='';document.getElementById('point-speech').value='';document.getElementById('point-photo').value='';document.getElementById('board-reviewed').checked=false;document.getElementById('point-gesture').value='';
    await loadPoints();
  }catch(e){pointStatus('保存失败：'+e.message,'bad');}
  finally{pointPoseBusy=false;syncPointButtons();}
}
fetch('/api/actions').then(r=>r.json()).then(body=>{for(const [id,gesture] of Object.entries(body.arm_gestures||{})){if(gesture.verified){const option=document.createElement('option');option.value=id;option.textContent=gesture.label;document.getElementById('point-gesture').append(option);}}});
let relocTimer=null, relocSeeds=[], relocPinRequired=false;
function showRelocSeed() {
  const id=document.getElementById('reloc-point').value,seed=relocSeeds.find(item=>item.id===id);
  const box=document.getElementById('reloc-seed');
  box.textContent=seed?`请将机器人放在“${seed.name}”附近并停稳：x=${seed.pose.x.toFixed(2)} m，y=${seed.pose.y.toFixed(2)} m，朝向=${(seed.pose.yaw*180/Math.PI).toFixed(1)}°。不是全场自动定位。`:'没有可用初值，请先确认起点。';
  return seed;
}
async function loadRelocation(refreshOptions=false) {
  clearTimeout(relocTimer);
  try {
    const response=await fetch('/api/relocation'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'读取失败');
    relocSeeds=body.seeds;relocPinRequired=body.requires_pin;
    if(refreshOptions){const select=document.getElementById('reloc-point'),previous=select.value;select.textContent='';
      for(const seed of relocSeeds){const option=document.createElement('option');option.value=seed.id;option.textContent=seed.id==='auto'?'自动：'+seed.name:seed.name;select.append(option);}
      if(relocSeeds.some(seed=>seed.id===previous))select.value=previous;
    }
    const seed=showRelocSeed(),state=body.relocation;
    const running=state.state==='running';document.getElementById('reloc-button').disabled=running||!seed;
    document.getElementById('reloc-point').disabled=running;
    const status=document.getElementById('reloc-status');status.textContent=state.message;status.className=state.state==='succeeded'?'ok':state.state==='failed'?'bad':'';
    if(state.pose)status.textContent+=`\n位置：(${state.pose.x.toFixed(2)}, ${state.pose.y.toFixed(2)})，朝向 ${(state.pose.yaw*180/Math.PI).toFixed(1)}°`;
    renderManualRelocation(state);
    if(running)relocTimer=setTimeout(()=>loadRelocation(),1000);
  }catch(e){document.getElementById('reloc-status').textContent='读取重定位状态失败：'+e.message;if(manualRelocationOwned){document.getElementById('manual-reloc-status').textContent='暂时无法读取重定位状态，正在重试：'+e.message;relocTimer=setTimeout(()=>loadRelocation(),2000);}}
}
async function relocalizeRobot() {
  const seed=showRelocSeed();if(!seed)return;
  if(!confirm(`确认机器人在“${seed.name}”附近、朝向正确且已停稳？\n这会停止导航并禁用运动，然后重定位。`))return;
  let pin='';if(relocPinRequired){pin=prompt('请输入操作员 PIN');if(pin===null)return;}
  document.getElementById('reloc-button').disabled=true;
  try{await post('/api/relocation/start',{point_id:document.getElementById('reloc-point').value,confirmed_position:true,pin});document.getElementById('confirm').textContent='';await loadRelocation();watchTask();}
  catch(e){document.getElementById('reloc-status').textContent='重定位请求失败：'+e.message;document.getElementById('reloc-status').className='bad';document.getElementById('reloc-button').disabled=false;}
}

let initializationState={state:'idle',stages:[],message:'尚未初始化'},initializationBusy=false,initializationTimer=null,initializationPinRequired=false;
let initialMap=null,initialMapImage=null,initialMapSelection=null,initialMapDragging=false,initialMapAllowed=false,initialMapLoading=false,initialMapSequence=0,initialMapZoom=1,initialLocalizedPose=null,manualRelocationOwned=false;
let initialMapPan=false,initialMapPanDrag=null;
let relocationSnapshot=null,lastManualVerification=null,manualVerificationExpiryTimer=null;
const MANUAL_VERIFICATION_MAX_AGE=300;
const INITIAL_PRIOR_RADIUS_M=1,INITIAL_PRIOR_YAW_RANGE_RAD=Math.PI/4;
function initializationRunning(){return initializationBusy||initializationState.state==='running';}
function syncInitializationButtons(){
  const ready=document.getElementById('initialize-ready').checked,running=initializationRunning();
  document.getElementById('initialize-start').disabled=!ready||running||manualRelocationOwned;
  document.getElementById('initialize-ready').disabled=running||manualRelocationOwned;
  document.getElementById('manual-reloc-start').disabled=!ready||running||manualRelocationOwned||initialMapLoading||!initialMapAllowed||!initialMapSelection||!Number.isFinite(initialMapSelection.yaw)||!document.getElementById('manual-reloc-confirmed').checked;
  document.getElementById('manual-reloc-confirmed').disabled=running||manualRelocationOwned;
}
function validInitialPose(pose){return pose&&['x','y','yaw'].every(key=>typeof pose[key]==='number'&&Number.isFinite(pose[key]));}
function verificationTime(timestamp){return new Date(timestamp*1000).toLocaleString('zh-CN',{hour12:false});}
function manualPriorDifference(verified){
  const prior=verified.seed&&verified.seed.pose,pose=verified.pose;
  if(!validInitialPose(prior)||!validInitialPose(pose))return '';
  const distance=Math.hypot(pose.x-prior.x,pose.y-prior.y),angle=Math.abs(Math.atan2(Math.sin(pose.yaw-prior.yaw),Math.cos(pose.yaw-prior.yaw)))*180/Math.PI;
  return `\n相对粗先验：位置修正 ${distance.toFixed(2)} m，朝向修正 ${angle.toFixed(1)}°（不是定位精度）。`;
}
function recentManualVerification(state){
  if(!state||state.state!=='succeeded'||!validInitialPose(state.pose)||!state.seed||state.seed.name!=='地图点选当前位置'||state.seed.frame_id!=='map'||!initialMap||initializationState.state!=='succeeded')return null;
  const started=state.started_at,finished=state.finished_at,initFinished=initializationState.finished_at,now=Date.now()/1000;
  if(![started,finished,initFinished].every(Number.isFinite)||started<initFinished||finished<started||finished>now+5||now-finished>MANUAL_VERIFICATION_MAX_AGE)return null;
  if(state.seed.map_fingerprint!==initialMap.map_fingerprint||initializationState.map_fingerprint!==initialMap.map_fingerprint)return null;
  return state;
}
function refreshInitializationPosition(){
  clearTimeout(manualVerificationExpiryTimer);
  lastManualVerification=recentManualVerification(relocationSnapshot);
  const position=document.getElementById('initialize-position'),status=document.getElementById('initialize-status'),manualStatus=document.getElementById('manual-reloc-status');
  status.textContent=initializationState.message||'尚未初始化';
  if(initializationState.state==='succeeded')status.textContent+='\n这是软件初始化结束时的记录，不是实时定位状态。';
  if(lastManualVerification){
    const verified=lastManualVerification,pose=verified.pose,time=verificationTime(verified.finished_at),difference=manualPriorDifference(verified);initialLocalizedPose=pose;
    status.textContent='软件初始化已完成；之后的人工重定位已核验成功。自动运动仍保持禁用。';status.className='ok';
    position.textContent=`人工重定位核验成功 · ${time}\n最终精配位姿：map (${pose.x.toFixed(2)}, ${pose.y.toFixed(2)}) m，朝向 ${(pose.yaw*180/Math.PI).toFixed(1)}°。${difference}\n这是核验时的历史结果，非实时位姿；当前位置请使用“读取当前位置”核对。`;
    manualStatus.textContent=`最近人工重定位已核验成功 · ${time}\n重定位核验结果（绿色箭头）：map (${pose.x.toFixed(2)}, ${pose.y.toFixed(2)}) m，朝向 ${(pose.yaw*180/Math.PI).toFixed(1)}°（非实时）。${difference}\n请真人核对箭头与现场墙体、障碍物及朝向是否一致；首次配准仍待实机验收，自动运动保持禁用。`;manualStatus.className='ok';
    manualVerificationExpiryTimer=setTimeout(()=>{lastManualVerification=null;refreshInitializationPosition();drawInitialMap();},Math.max(1,(verified.finished_at+MANUAL_VERIFICATION_MAX_AGE-Date.now()/1000)*1000));
  }else{
    initialLocalizedPose=null;
    const now=Date.now()/1000,finished=initializationState.finished_at;
    const newerAttempt=relocationSnapshot&&Number.isFinite(relocationSnapshot.started_at)&&Number.isFinite(initializationState.started_at)&&relocationSnapshot.started_at>=initializationState.started_at&&['running','failed','cancelled'].includes(relocationSnapshot.state);
    if(newerAttempt){
      position.textContent=relocationSnapshot.state==='running'?'重定位正在进行；旧位姿不用于当前状态，等待本次核验。':'最近重定位未完成有效核验；旧记录不作为当前位置，请显式读取定位状态或重新确认初值。';
    }else if(initializationState.localized===true&&validInitialPose(initializationState.pose)&&Number.isFinite(finished)&&finished<=now+5&&now-finished<=MANUAL_VERIFICATION_MAX_AGE&&initialMap&&initializationState.map_fingerprint===initialMap.map_fingerprint){
      initialLocalizedPose=initializationState.pose;position.textContent=`初始化时核验结果 · ${verificationTime(finished)}（非实时）\nmap (${initialLocalizedPose.x.toFixed(2)}, ${initialLocalizedPose.y.toFixed(2)}) m，朝向 ${(initialLocalizedPose.yaw*180/Math.PI).toFixed(1)}°。不会自动重新配准；当前位姿请显式读取。`;
    }else{
      position.textContent=initializationState.needs_initial_pose||initializationState.state==='needs_initial_pose'?'初始化结束时尚未取得定位初值；这是历史快照，不代表后续重定位结果。请核对最新人工核验或显式读取当前位置。未定位 TF 零点不是实际地图位置。':'初始化快照不是实时定位状态；当前位姿请显式读取，不使用未定位 TF 零点。';
    }
    if(!manualRelocationOwned&&relocationSnapshot&&relocationSnapshot.state==='succeeded'&&relocationSnapshot.seed&&relocationSnapshot.seed.name==='地图点选当前位置'){
      manualStatus.textContent='历史人工重定位记录未作为本次状态使用：须在同一地图、本次初始化之后且 5 分钟以内完成核验。当前位姿请显式读取。';manualStatus.className='hint';
    }
    if(!manualRelocationOwned&&relocationSnapshot&&relocationSnapshot.seed&&relocationSnapshot.seed.name!=='地图点选当前位置'){manualStatus.textContent='';manualStatus.className='hint';}
  }
}
function renderInitialization(state){
  initializationState=state||{state:'idle',stages:[],message:'尚未初始化'};
  const status=document.getElementById('initialize-status');status.textContent=initializationState.message||'尚未初始化';status.className=initializationState.state==='failed'?'bad':initializationState.state==='succeeded'?'ok':'';
  const list=document.getElementById('initialize-stages');list.textContent='';
  const stages=Array.isArray(initializationState.stages)?initializationState.stages:Object.entries(initializationState.stages||{}).map(([name,value])=>typeof value==='object'?{name,...value}:{name,state:value});
  for(const stage of stages){const item=document.createElement('li');item.textContent=(stage.label||stage.name||stage.id||'阶段')+' — '+(stateLabels[stage.state]||stage.state||'等待')+(stage.message?' · '+stage.message:'');list.append(item);}
  initialMapAllowed=['succeeded','needs_initial_pose'].includes(initializationState.state);
  document.getElementById('initial-map-panel').hidden=!initialMapAllowed;
  refreshInitializationPosition();
  syncInitializationButtons();
  if(initialMapAllowed){if(!initialMap&&!initialMapLoading)loadInitialMap();else drawInitialMap();}
}
async function loadInitialization(){
  clearTimeout(initializationTimer);
  try{
    const response=await fetch('/api/init'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'初始化状态读取失败');
    initializationPinRequired=body.requires_pin===true;renderInitialization(body.initialization);
    if(initializationState.state==='running')initializationTimer=setTimeout(loadInitialization,1000);
  }catch(e){document.getElementById('initialize-status').textContent='读取初始化状态失败：'+e.message;if(initializationState.state==='running')initializationTimer=setTimeout(loadInitialization,2000);}
}
function initializationPin(){
  const pin=document.getElementById('initialize-pin').value.trim();
  if(initializationPinRequired&&!pin)throw new Error('请填写操作员 PIN');
  return pin;
}
async function startInitialization(){
  if(initializationRunning()||manualRelocationOwned)return;
  const status=document.getElementById('initialize-status');
  if(!document.getElementById('initialize-ready').checked){status.textContent='请先确认双脚落地、官方可运动模式与停稳，再启动初始化。';return;}
  try{
    const pin=initializationPin();initializationBusy=true;syncInitializationButtons();
    lastManualVerification=null;clearTimeout(manualVerificationExpiryTimer);
    initialMapSequence++;initialMapLoading=false;initialMap=null;initialMapImage=null;initialMapPanDrag=null;initialMapDragging=false;
    initialMapAllowed=false;initialMapSelection=null;document.getElementById('manual-reloc-confirmed').checked=false;document.getElementById('initial-map-panel').hidden=true;
    status.textContent='正在请求软件初始化；自动运动保持禁用……';
    const body=await post('/api/init/start',{robot_ready:true,pin});renderInitialization(body.initialization||{state:'running',stages:[],message:body.message});await loadInitialization();
  }catch(e){status.textContent='初始化失败：'+e.message;status.className='bad';}
  finally{initializationBusy=false;syncInitializationButtons();}
}
function validateInitialMap(map){
  if(!map||map.frame_id!=='map'||! /^[0-9a-f]{64}$/.test(map.map_fingerprint)||!map.origin||!['x','y','yaw'].every(key=>Number.isFinite(map.origin[key]))||!Number.isFinite(map.resolution)||map.resolution<=0||!Number.isInteger(map.width)||!Number.isInteger(map.height)||map.width<=0||map.height<=0||map.image_url!=='/api/map/image')throw new Error('地图坐标、指纹或图片接口无效');
  return map;
}
async function loadInitialMap(){
  if(initialMapLoading||!initialMapAllowed)return;
  initialMapLoading=true;const sequence=++initialMapSequence;
  syncInitializationButtons();
  const status=document.getElementById('initial-map-status');status.textContent='正在读取地图图片与坐标……';
  try{
    const response=await fetch('/api/map'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'地图读取失败');
    const map=validateInitialMap(body.map),image=new Image();
    await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('地图 PNG 图片加载超时，请重试')),15000);image.onload=()=>{clearTimeout(timer);resolve();};image.onerror=()=>{clearTimeout(timer);reject(new Error('地图 PNG 图片加载失败'));};image.src=map.image_url+'?fingerprint='+encodeURIComponent(map.map_fingerprint);});
    if(sequence!==initialMapSequence)return;
    if(!image.naturalWidth||!image.naturalHeight)throw new Error('地图图片尺寸无效');
    initialMap=map;initialMapImage=image;initialMapSelection=null;document.getElementById('manual-reloc-confirmed').checked=false;
    const canvas=document.getElementById('initial-map');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;zoomInitialMap(0);
    status.textContent=`map · ${map.width} × ${map.height} 网格 · ${map.resolution.toFixed(3)} m/格。请圈定大致站位（固定半径 1 米），再拖出大致朝向。`;
    document.getElementById('initial-map-pose').textContent='尚未选择位置与朝向。';refreshInitializationPosition();drawInitialMap();syncInitializationButtons();
  }catch(e){if(sequence===initialMapSequence){initialMap=null;initialMapImage=null;status.textContent='地图加载失败：'+e.message;}}
  finally{if(sequence===initialMapSequence){initialMapLoading=false;syncInitializationButtons();}}
}
function worldFromCanvas(cx,cy,map=initialMap,canvas=document.getElementById('initial-map')){
  if(!map||!canvas.width||!canvas.height)throw new Error('地图尚未加载');
  const localX=cx*map.width/canvas.width*map.resolution,localY=(map.height-cy*map.height/canvas.height)*map.resolution;
  const cosine=Math.cos(map.origin.yaw),sine=Math.sin(map.origin.yaw);
  return {x:map.origin.x+cosine*localX-sine*localY,y:map.origin.y+sine*localX+cosine*localY};
}
function canvasFromWorld(x,y,map=initialMap,canvas=document.getElementById('initial-map')){
  const dx=x-map.origin.x,dy=y-map.origin.y,cosine=Math.cos(map.origin.yaw),sine=Math.sin(map.origin.yaw);
  const localX=cosine*dx+sine*dy,localY=-sine*dx+cosine*dy;
  return {cx:localX/map.resolution*canvas.width/map.width,cy:(map.height-localY/map.resolution)*canvas.height/map.height};
}
function initialPriorCircle(selected,map=initialMap,canvas=document.getElementById('initial-map')){
  const center=canvasFromWorld(selected.x,selected.y,map,canvas);
  return {...center,radiusX:INITIAL_PRIOR_RADIUS_M/map.resolution*canvas.width/map.width,radiusY:INITIAL_PRIOR_RADIUS_M/map.resolution*canvas.height/map.height};
}
function zoomInitialMap(factor){
  initialMapZoom=factor===0?1:Math.max(1,Math.min(8,initialMapZoom*factor));
  document.getElementById('initial-map').style.width=(100*initialMapZoom)+'%';drawInitialMap();
}
function toggleInitialMapPan(){
  initialMapPan=!initialMapPan;initialMapDragging=false;initialMapPanDrag=null;
  document.getElementById('initial-map-pan').textContent=initialMapPan?'切换为选择位姿':'切换为拖动平移';
  document.getElementById('initial-map').style.cursor=initialMapPan?'grab':'crosshair';
}
function drawInitialMap(){
  if(!initialMap||!initialMapImage)return;
  const canvas=document.getElementById('initial-map'),context=canvas.getContext('2d');if(!context)return;
  context.clearRect(0,0,canvas.width,canvas.height);context.drawImage(initialMapImage,0,0,canvas.width,canvas.height);
  const rect=canvas.getBoundingClientRect(),scale=canvas.width/(rect.width||Math.min(960,canvas.width));
  function dot(cx,cy,color,label){context.fillStyle=color;context.beginPath();context.arc(cx,cy,4*scale,0,Math.PI*2);context.fill();if(label){context.font=(12*scale)+'px sans-serif';context.fillText(label,cx+7*scale,cy-5*scale);}}
  function arrow(anchor,tip,color){context.strokeStyle=color;context.lineWidth=3*scale;context.beginPath();context.moveTo(anchor.cx,anchor.cy);context.lineTo(tip.cx,tip.cy);const angle=Math.atan2(tip.cy-anchor.cy,tip.cx-anchor.cx),size=10*scale;context.moveTo(tip.cx-size*Math.cos(angle-.45),tip.cy-size*Math.sin(angle-.45));context.lineTo(tip.cx,tip.cy);context.lineTo(tip.cx-size*Math.cos(angle+.45),tip.cy-size*Math.sin(angle+.45));context.stroke();dot(anchor.cx,anchor.cy,color);}
  savedPoints.forEach((point,index)=>{if(point.map_fingerprint&&point.map_fingerprint!==initialMap.map_fingerprint)return;const pixel=canvasFromWorld(point.pose.x,point.pose.y);dot(pixel.cx,pixel.cy,'#126dce',String(index+1)+' · '+String(point.name).slice(0,8));});
  if(initialMapSelection){const selected=initialMapSelection,circle=initialPriorCircle(selected);context.fillStyle='rgba(255,153,0,0.14)';context.strokeStyle='rgba(255,153,0,0.75)';context.lineWidth=1.5*scale;context.beginPath();context.ellipse(circle.cx,circle.cy,circle.radiusX,circle.radiusY,0,0,Math.PI*2);context.fill();context.stroke();if(selected.tip)arrow(selected.anchor,selected.tip,'#ff9900');else dot(selected.anchor.cx,selected.anchor.cy,'#ff9900');}
  if(validInitialPose(initialLocalizedPose)){const pixel=canvasFromWorld(initialLocalizedPose.x,initialLocalizedPose.y),angle=initialLocalizedPose.yaw-initialMap.origin.yaw;arrow(pixel,{cx:pixel.cx+30*scale*Math.cos(angle),cy:pixel.cy-30*scale*Math.sin(angle)},'#188553');}
}
function initialCanvasPoint(event){
  const canvas=document.getElementById('initial-map'),rect=canvas.getBoundingClientRect();
  if(!rect.width||!rect.height)throw new Error('地图显示尺寸无效');
  return {cx:Math.max(0,Math.min(canvas.width,(event.clientX-rect.left)*canvas.width/rect.width)),cy:Math.max(0,Math.min(canvas.height,(event.clientY-rect.top)*canvas.height/rect.height))};
}
function initialMapPointerDown(event){
  if((event.button!==undefined&&event.button!==0)||initialMapLoading||!initialMapAllowed||!initialMap||initializationRunning()||manualRelocationOwned)return;
  const canvas=document.getElementById('initial-map'),anchor=initialCanvasPoint(event),world=worldFromCanvas(anchor.cx,anchor.cy);
  if(initialMapPan){const wrap=document.getElementById('initial-map-wrap');initialMapPanDrag={x:event.clientX,y:event.clientY,left:wrap.scrollLeft||0,top:wrap.scrollTop||0};if(canvas.setPointerCapture)canvas.setPointerCapture(event.pointerId);if(event.preventDefault)event.preventDefault();return;}
  initialMapDragging=true;initialMapSelection={anchor,tip:null,x:world.x,y:world.y,yaw:null,map_fingerprint:initialMap.map_fingerprint};
  document.getElementById('manual-reloc-confirmed').checked=false;
  if(canvas.setPointerCapture)canvas.setPointerCapture(event.pointerId);
  if(event.preventDefault)event.preventDefault();showInitialSelection();
}
function initialMapPointerMove(event){
  if(initialMapPanDrag){const wrap=document.getElementById('initial-map-wrap');wrap.scrollLeft=initialMapPanDrag.left-(event.clientX-initialMapPanDrag.x);wrap.scrollTop=initialMapPanDrag.top-(event.clientY-initialMapPanDrag.y);if(event.preventDefault)event.preventDefault();return;}
  if(!initialMapDragging||!initialMapSelection)return;
  const canvas=document.getElementById('initial-map'),rect=canvas.getBoundingClientRect(),tip=initialCanvasPoint(event),anchor=initialMapSelection.anchor;
  initialMapSelection.tip=tip;
  const distance=Math.hypot((tip.cx-anchor.cx)*rect.width/canvas.width,(tip.cy-anchor.cy)*rect.height/canvas.height);
  if(distance>=8){const end=worldFromCanvas(tip.cx,tip.cy);initialMapSelection.yaw=Math.atan2(end.y-initialMapSelection.y,end.x-initialMapSelection.x);}else initialMapSelection.yaw=null;
  document.getElementById('manual-reloc-confirmed').checked=false;if(event.preventDefault)event.preventDefault();showInitialSelection();
}
function initialMapPointerUp(event){
  if(initialMapPanDrag){initialMapPointerMove(event);initialMapPanDrag=null;const canvas=document.getElementById('initial-map');if(canvas.releasePointerCapture&&canvas.hasPointerCapture&&canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);return;}
  if(!initialMapDragging)return;initialMapPointerMove(event);initialMapDragging=false;
  const canvas=document.getElementById('initial-map');if(canvas.releasePointerCapture&&canvas.hasPointerCapture&&canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
}
function showInitialSelection(){
  const selected=initialMapSelection;
  document.getElementById('initial-map-pose').textContent=selected?`粗定位先验（未配准，非导航目标）：圆心 x=${selected.x.toFixed(2)} m，y=${selected.y.toFixed(2)} m；固定搜索半径 ${INITIAL_PRIOR_RADIUS_M.toFixed(1)} m`+(Number.isFinite(selected.yaw)?`；yaw=${selected.yaw.toFixed(3)} rad（${(selected.yaw*180/Math.PI).toFixed(1)}°），朝向范围 ±${(INITIAL_PRIOR_YAW_RANGE_RAD*180/Math.PI).toFixed(0)}°。`:'。请从大致站位朝实际朝向拖出箭头。'):'尚未选择位置与朝向。';
  drawInitialMap();syncInitializationButtons();
}
async function submitManualRelocation(){
  const status=document.getElementById('manual-reloc-status');
  if(manualRelocationOwned||initializationRunning())return;
  try{
    if(!document.getElementById('initialize-ready').checked)throw new Error('请先确认机器人双脚落地、官方运动模式且停稳');
    if(initialMapLoading||!initialMapAllowed||!initialMapSelection||!Number.isFinite(initialMapSelection.yaw)||!initialMap||initialMapSelection.map_fingerprint!==initialMap.map_fingerprint)throw new Error('请在当前地图选择大致站位并拖出大致朝向');
    if(!document.getElementById('manual-reloc-confirmed').checked)throw new Error('请确认真实位置在 1 米圈内，实际朝向与箭头约 ±45° 内，且机器人已停稳');
    const selected=initialMapSelection,pin=initializationPin();manualRelocationOwned=true;syncInitializationButtons();status.textContent='正在提交 1 米粗定位先验，等待雷达匹配与最终精配核验；不会行走，失败不会自动扩大搜索……';
    const body=await post('/api/relocation/manual',{x:selected.x,y:selected.y,yaw:selected.yaw,map_fingerprint:selected.map_fingerprint,confirmed_position:true,pin});
    document.getElementById('confirm').textContent='';if(body.relocation)renderManualRelocation(body.relocation);await loadRelocation();
  }catch(e){manualRelocationOwned=false;status.textContent='点选重定位失败：'+e.message;status.className='bad';syncInitializationButtons();}
}
function renderManualRelocation(state){
  relocationSnapshot=state;const owned=manualRelocationOwned;
  if(owned){const status=document.getElementById('manual-reloc-status');status.textContent=state.message||'正在核验定位……';status.className=state.state==='succeeded'?'ok':state.state==='failed'?'bad':'';}
  if(owned&&['succeeded','failed','cancelled'].includes(state.state)){manualRelocationOwned=false;document.getElementById('manual-reloc-confirmed').checked=false;syncInitializationButtons();}
  refreshInitializationPosition();drawInitialMap();
}
const initialCanvas=document.getElementById('initial-map');
initialCanvas.addEventListener('pointerdown',initialMapPointerDown);initialCanvas.addEventListener('pointermove',initialMapPointerMove);initialCanvas.addEventListener('pointerup',initialMapPointerUp);initialCanvas.addEventListener('pointercancel',()=>{if(initialMapPanDrag){initialMapPanDrag=null;return;}initialMapDragging=false;initialMapSelection=null;document.getElementById('manual-reloc-confirmed').checked=false;showInitialSelection();});
window.addEventListener('resize',drawInitialMap);

// Read-only live view. Its polling lifetime is separate from initialization.
let liveMap=null,liveMapImage=null,liveMapSnapshot=null,liveMapReceivedAt=0,liveMapZoom=1,liveMapFollow=false,liveMapPan=null,liveMapInitialCentered=false;
let liveMapSequence=0,liveMapTimer=null,liveMapExpiryTimer=null,liveMapController=null,liveMapInFlight=false,liveMapMismatchRefreshes=0;
const LIVE_POSE_TTL=1.5,LIVE_SCAN_TTL=1.5,LIVE_COSTMAP_TTL=2.5;
function liveMapVisible(){return document.body.dataset.page==='map'&&!document.hidden;}
function liveMapNow(){return typeof performance!=='undefined'&&typeof performance.now==='function'?performance.now():Date.now();}
function liveMapElapsed(){return Math.max(0,(liveMapNow()-liveMapReceivedAt)/1000);}
function liveMapFresh(value,ttl){return !!value&&Number.isFinite(value.age_seconds)&&value.age_seconds>=0&&value.age_seconds+liveMapElapsed()<=ttl;}
function liveMapPose(){return liveMapSnapshot&&liveMapSnapshot.state==='ready'&&liveMap&&liveMapSnapshot.map_fingerprint===liveMap.map_fingerprint&&liveMapFresh(liveMapSnapshot.pose,LIVE_POSE_TTL)?liveMapSnapshot.pose:null;}
function clearLiveOverlay(message){
  liveMapSnapshot=null;liveMapReceivedAt=0;clearTimeout(liveMapExpiryTimer);liveMapExpiryTimer=null;
  document.getElementById('live-map-status').textContent=message;document.getElementById('live-map-status').className='warning';
  document.getElementById('live-map-position').textContent='当前位置未知或过期；实时位姿和障碍叠加已隐藏。';
  document.getElementById('live-map-sensors').textContent='传感器实时状态未知。';drawLiveMap();
}
function stopLiveMap(){
  ++liveMapSequence;clearTimeout(liveMapTimer);liveMapTimer=null;clearTimeout(liveMapExpiryTimer);liveMapExpiryTimer=null;
  if(liveMapController)liveMapController.abort();liveMapController=null;liveMapInFlight=false;liveMapPan=null;
  clearLiveOverlay('实时地图已暂停；仅在此页面可见时读取。');
}
function liveMapSchedule(sequence,delay=500){
  clearTimeout(liveMapTimer);if(sequence===liveMapSequence&&liveMapVisible())liveMapTimer=setTimeout(()=>pollLiveMap(sequence),delay);
}
function validateLiveSnapshot(live){
  if(!live||!['ready','unlocalized','stale','starting','offline'].includes(live.state)||live.frame_id!=='map'||typeof live.message!=='string'||live.message.length>1200)throw new Error('实时地图状态格式无效');
  if((live.map_fingerprint!==null&&! /^[0-9a-f]{64}$/.test(live.map_fingerprint))||(live.updated_at!==null&&!Number.isFinite(live.updated_at))||(live.state==='ready'&&(!live.map_fingerprint||live.updated_at===null||!live.pose)))throw new Error('实时地图定位上下文无效');
  const pose=live.pose;
  if(pose!==null&&(!pose||!['x','y','yaw','stamp','age_seconds'].every(key=>Number.isFinite(pose[key]))||Math.abs(pose.x)>1e6||Math.abs(pose.y)>1e6||pose.stamp<0||pose.age_seconds<0))throw new Error('实时位姿格式无效');
  for(const [key,limit] of [['scan',400],['costmap',600]]){
    const sensor=live[key];
    if(!sensor||typeof sensor.state!=='string'||sensor.state.length>80||!Array.isArray(sensor.points)||sensor.points.length>limit)throw new Error('实时障碍格式无效');
    if(sensor.stamp!==null&&(!Number.isFinite(sensor.stamp)||sensor.stamp<0))throw new Error('实时障碍时间无效');
    if(sensor.age_seconds!==null&&(!Number.isFinite(sensor.age_seconds)||sensor.age_seconds<0))throw new Error('实时障碍年龄无效');
    if(sensor.points.some(point=>!Array.isArray(point)||point.length!==2||point.some(value=>!Number.isFinite(value)||Math.abs(value)>1e6)))throw new Error('实时障碍坐标无效');
  }
  return live;
}
function simplifyLiveMap(image,map){
  if(![0,1].includes(map.negate)||!Number.isFinite(map.free_thresh)||!Number.isFinite(map.occupied_thresh)||map.free_thresh<0||map.occupied_thresh>1||map.free_thresh>=map.occupied_thresh||map.width*map.height>20000000)throw new Error('地图占用阈值或大小无效');
  const background=document.createElement('canvas');background.width=map.width;background.height=map.height;
  const context=background.getContext('2d');context.drawImage(image,0,0,map.width,map.height);const pixels=context.getImageData(0,0,map.width,map.height),data=pixels.data;
  const wall=[45,55,69],free=[240,244,247],unknown=[160,171,183];
  for(let i=0;i<data.length;i+=4){const grey=(data[i]+data[i+1]+data[i+2])/3,occupancy=map.negate?grey/255:(255-grey)/255;const color=occupancy>map.occupied_thresh?wall:occupancy<map.free_thresh?free:unknown;data[i]=color[0];data[i+1]=color[1];data[i+2]=color[2];data[i+3]=255;}
  context.putImageData(pixels,0,0);return background;
}
async function loadLiveMap(sequence){
  if(sequence!==liveMapSequence||!liveMapVisible()||liveMapInFlight)return false;
  liveMapInFlight=true;const controller=new AbortController();liveMapController=controller;const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('/api/map',{signal:controller.signal,cache:'no-store'}),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'底图读取失败');
    const map=validateInitialMap(body.map),image=new Image();
    await new Promise((resolve,reject)=>{
      const abort=()=>{image.src='';reject(new Error('底图读取已取消'));};controller.signal.addEventListener('abort',abort,{once:true});
      image.onload=()=>{controller.signal.removeEventListener('abort',abort);resolve();};image.onerror=()=>{controller.signal.removeEventListener('abort',abort);reject(new Error('地图图片读取失败'));};
      image.src=map.image_url+'?fingerprint='+encodeURIComponent(map.map_fingerprint);
    });
    if(sequence!==liveMapSequence||!liveMapVisible())return false;
    if(image.naturalWidth!==map.width||image.naturalHeight!==map.height)throw new Error('底图尺寸与坐标不一致');
    const background=simplifyLiveMap(image,map);liveMap=map;liveMapImage=background;liveMapSnapshot=null;
    const canvas=document.getElementById('live-map');canvas.width=map.width;canvas.height=map.height;zoomLiveMap(0);liveMapInitialCentered=false;drawLiveMap();return true;
  }catch(e){if(sequence===liveMapSequence){liveMap=null;liveMapImage=null;clearLiveOverlay('底图加载失败：'+e.message);}return false;}
  finally{clearTimeout(timeout);if(sequence===liveMapSequence){liveMapInFlight=false;liveMapController=null;}}
}
async function startLiveMap(){
  stopLiveMap();if(!liveMapVisible())return;const sequence=liveMapSequence;liveMapInitialCentered=false;clearLiveOverlay('正在读取固定底图与实时状态……');
  if(!liveMap||!liveMapImage){if(!await loadLiveMap(sequence))return;}
  if(sequence===liveMapSequence&&liveMapVisible())pollLiveMap(sequence);
}
function refreshLiveMap(){liveMap=null;liveMapImage=null;liveMapMismatchRefreshes=0;startLiveMap();}
async function pollLiveMap(sequence=liveMapSequence){
  if(sequence!==liveMapSequence||!liveMapVisible()||liveMapInFlight)return;
  clearTimeout(liveMapTimer);liveMapTimer=null;
  if(!liveMap){clearLiveOverlay('底图尚未就绪，请重新读取地图。');return;}
  liveMapInFlight=true;const controller=new AbortController();liveMapController=controller;const timeout=setTimeout(()=>controller.abort(),2000);let mismatch=false;
  try{
    const response=await fetch('/api/map/live',{signal:controller.signal,cache:'no-store'}),body=await response.json();
    if(sequence!==liveMapSequence||!liveMapVisible())return;
    if(!response.ok||body.status!=='success')throw new Error(body.message||'实时状态读取失败');
    const live=validateLiveSnapshot(body.live);
    if(live.map_fingerprint!==null&&live.map_fingerprint!==liveMap.map_fingerprint){clearLiveOverlay('地图指纹不匹配，实时叠加已停止；正在核对底图。');mismatch=true;}
    else{liveMapSnapshot=live;liveMapReceivedAt=liveMapNow();liveMapMismatchRefreshes=0;renderLiveMapState();if(liveMapPose()&&!liveMapInitialCentered){zoomLiveMap(Math.max(1,Math.min(16,liveMap.width*liveMap.resolution/12))/liveMapZoom);centerLiveRobot();}else if(liveMapFollow&&liveMapPose())centerLiveRobot();}
  }catch(e){if(sequence===liveMapSequence)clearLiveOverlay('实时读取失败，当前位置未知：'+e.message);}
  finally{clearTimeout(timeout);if(sequence===liveMapSequence){liveMapInFlight=false;liveMapController=null;}}
  if(sequence!==liveMapSequence||!liveMapVisible())return;
  if(mismatch){
    if(++liveMapMismatchRefreshes>2){clearLiveOverlay('地图指纹持续不匹配，已停止实时叠加；请点击“重新读取地图”。');return;}
    if(!await loadLiveMap(sequence))return;
  }
  liveMapSchedule(sequence);
}
function renderLiveMapState(){
  if(!liveMapSnapshot)return;const live=liveMapSnapshot,pose=liveMapPose(),status=document.getElementById('live-map-status'),position=document.getElementById('live-map-position');
  status.textContent=pose?'定位有效 · '+live.message:(live.state==='ready'?'位姿已过期；实时叠加已隐藏。':live.message||'当前位置未知');status.className=pose?'ok':'warning';
  position.textContent=pose?`map · x=${pose.x.toFixed(2)} m，y=${pose.y.toFixed(2)} m，朝向=${(pose.yaw*180/Math.PI).toFixed(1)}° · 数据年龄 ${(pose.age_seconds+liveMapElapsed()).toFixed(2)} s`:'当前位置未知或过期；不会显示未定位的 TF 零点。';
  const stateText=(sensor,ttl)=>sensor.state==='ready'&&liveMapFresh(sensor,ttl)?`新鲜 · ${(sensor.age_seconds+liveMapElapsed()).toFixed(2)} s`:'未知 / 过期（'+sensor.state+'）';
  document.getElementById('live-map-sensors').textContent='雷达：'+stateText(live.scan,LIVE_SCAN_TTL)+'；局部占用：'+stateText(live.costmap,LIVE_COSTMAP_TTL)+'。灰色区域尚无可靠底图。';
  drawLiveMap();clearTimeout(liveMapExpiryTimer);liveMapExpiryTimer=null;
  const remaining=[[live.pose,LIVE_POSE_TTL],[live.scan,LIVE_SCAN_TTL],[live.costmap,LIVE_COSTMAP_TTL]].filter(([value,ttl])=>liveMapFresh(value,ttl)).map(([value,ttl])=>Math.max(5,(ttl-value.age_seconds-liveMapElapsed())*1000+5));
  if(remaining.length&&liveMapVisible())liveMapExpiryTimer=setTimeout(renderLiveMapState,Math.min(...remaining));
}
function drawLiveMap(){
  if(!liveMap||!liveMapImage)return;const canvas=document.getElementById('live-map'),context=canvas.getContext('2d');if(!context)return;
  context.clearRect(0,0,canvas.width,canvas.height);context.drawImage(liveMapImage,0,0,canvas.width,canvas.height);
  const rect=canvas.getBoundingClientRect(),scale=canvas.width/(rect.width||Math.min(960,canvas.width));
  const pixel=(x,y)=>canvasFromWorld(x,y,liveMap,canvas),inside=p=>p.cx>=0&&p.cy>=0&&p.cx<canvas.width&&p.cy<canvas.height;
  function dot(p,color,radius){if(!inside(p))return;context.fillStyle=color;context.beginPath();context.arc(p.cx,p.cy,radius*scale,0,Math.PI*2);context.fill();}
  savedPoints.slice(0,100).forEach((point,index)=>{if(point.map_fingerprint!==liveMap.map_fingerprint||!point.pose||!Number.isFinite(point.pose.x)||!Number.isFinite(point.pose.y))return;const p=pixel(point.pose.x,point.pose.y);if(!inside(p))return;dot(p,'#126dce',4);context.fillStyle='#126dce';context.font=(11*scale)+'px sans-serif';context.fillText(String(index+1)+(liveMapZoom>=4?' · '+String(point.name).slice(0,10):''),p.cx+7*scale,p.cy-5*scale);});
  const pose=liveMapPose();if(!pose)return;
  for(const [key,color,ttl] of [['scan','#f69b24',LIVE_SCAN_TTL],['costmap','#dc4141',LIVE_COSTMAP_TTL]]){const sensor=liveMapSnapshot[key];if(sensor.state==='ready'&&liveMapFresh(sensor,ttl))sensor.points.forEach(point=>dot(pixel(point[0],point[1]),color,2));}
  const p=pixel(pose.x,pose.y),angle=pose.yaw-liveMap.origin.yaw;if(!inside(p))return;
  const tip={cx:p.cx+23*scale*Math.cos(angle),cy:p.cy-23*scale*Math.sin(angle)};context.strokeStyle='#188553';context.lineWidth=3*scale;context.beginPath();context.moveTo(p.cx,p.cy);context.lineTo(tip.cx,tip.cy);
  for(const side of [-.55,.55]){context.moveTo(tip.cx-10*scale*Math.cos(angle+side),tip.cy+10*scale*Math.sin(angle+side));context.lineTo(tip.cx,tip.cy);}context.stroke();dot(p,'#188553',5);
}
function zoomLiveMap(factor){liveMapInitialCentered=true;liveMapZoom=factor===0?1:Math.max(1,Math.min(16,liveMapZoom*factor));document.getElementById('live-map').style.width=(100*liveMapZoom)+'%';drawLiveMap();if(liveMapFollow)centerLiveRobot();}
function centerLiveRobot(){
  const pose=liveMapPose();if(!pose){document.getElementById('live-map-status').textContent='当前位置未知或过期，无法机器人居中。';return;}
  const canvas=document.getElementById('live-map'),wrap=document.getElementById('live-map-wrap'),p=canvasFromWorld(pose.x,pose.y,liveMap,canvas),rect=canvas.getBoundingClientRect();
  wrap.scrollLeft=Math.max(0,p.cx/canvas.width*rect.width-wrap.clientWidth/2);wrap.scrollTop=Math.max(0,p.cy/canvas.height*rect.height-wrap.clientHeight/2);
}
function toggleLiveMapFollow(){liveMapFollow=!liveMapFollow;document.getElementById('live-map-follow').textContent=liveMapFollow?'关闭跟随':'开启跟随';if(liveMapFollow)centerLiveRobot();}
function liveMapPointerDown(event){if(event.button!==undefined&&event.button!==0)return;const canvas=document.getElementById('live-map'),wrap=document.getElementById('live-map-wrap');liveMapInitialCentered=true;liveMapFollow=false;document.getElementById('live-map-follow').textContent='开启跟随';liveMapPan={x:event.clientX,y:event.clientY,left:wrap.scrollLeft||0,top:wrap.scrollTop||0};if(canvas.setPointerCapture)canvas.setPointerCapture(event.pointerId);if(event.preventDefault)event.preventDefault();}
function liveMapPointerMove(event){if(!liveMapPan)return;const wrap=document.getElementById('live-map-wrap');wrap.scrollLeft=liveMapPan.left-(event.clientX-liveMapPan.x);wrap.scrollTop=liveMapPan.top-(event.clientY-liveMapPan.y);if(event.preventDefault)event.preventDefault();}
function liveMapPointerUp(event){if(!liveMapPan)return;liveMapPointerMove(event);liveMapPan=null;}
const liveCanvas=document.getElementById('live-map');liveCanvas.addEventListener('pointerdown',liveMapPointerDown);liveCanvas.addEventListener('pointermove',liveMapPointerMove);liveCanvas.addEventListener('pointerup',liveMapPointerUp);liveCanvas.addEventListener('pointercancel',()=>{liveMapPan=null;});
document.addEventListener('visibilitychange',()=>{if(document.body.dataset.page==='map'){if(document.hidden)stopLiveMap();else startLiveMap();}});
window.addEventListener('resize',()=>{drawLiveMap();if(liveMapFollow)centerLiveRobot();});

const routeInfo={
  '/':{key:'home',title:'工作台',description:'从一个入口开始管理导览、讲解和机器人状态。'},
  '/initialize':{key:'initialize',title:'一键初始化',description:'明确确认机器人已准备就绪，再启动软件模块；必要时在地图上确认真实站位与朝向。'},
  '/map':{key:'map',title:'实时地图',description:'只读查看固定墙体底图、机器人的当前大致位置与实时探测障碍；不会控制运动。'},
  '/navigation':{key:'navigation',title:'点位导航',description:'选择已登记导览点，以当前提速档自主规划移动；独立于 Omni 和语音讲解。'},
  '/assistant':{key:'assistant',title:'导览助手',description:'文字、照片或录音输入，由 Omni 回答或生成待确认计划。'},
  '/agent':{key:'agent',title:'Agent 导览',description:'以导览点位姿和展板先验为依据，由 Omni 临场构思，再确认导航与讲解任务。'},
  '/knowledge':{key:'knowledge',title:'先验与文献',description:'查看点位知识来源，并上传后续讲解所需的参考文献。'},
  '/tasks':{key:'tasks',title:'任务控制',description:'先预览与模拟，再由操作员确认执行。'},
  '/points':{key:'points',title:'导览点管理',description:'查看已保存的展板资料、讲解和 map 坐标。'},
  '/points/new':{key:'point_new',title:'添加导览点',description:'停稳、填写资料、核对讲解，最后采集当前位置保存。'},
  '/localization':{key:'localization',title:'定位与重定位',description:'读取实时位姿，或在已确认的位置重新定位。'},
  '/system':{key:'system',title:'系统状态',description:'检查 PC2 连接与各模块状态；不会启动运动。'}
};
function activatePage(path){
  const wasLive=document.body.dataset.page==='map';
  const info=routeInfo[path]||routeInfo['/'];document.body.dataset.page=info.key;
  for(const view of document.querySelectorAll('[data-view]'))view.hidden=view.dataset.view!==info.key;
  const inTasks=['assistant','tasks','agent','navigation'].includes(info.key);document.getElementById('plan-panel').hidden=!inTasks;
  document.getElementById('page-title').textContent=info.title;document.getElementById('page-description').textContent=info.description;
  document.title=info.title+' · DaoLan';
  for(const link of document.querySelectorAll('[data-nav]')){
    const href=link.getAttribute('href');
    if(href===path)link.setAttribute('aria-current','page');
    else if(info.key==='point_new'&&href==='/points')link.setAttribute('aria-current','location');
    else link.removeAttribute('aria-current');
  }
  if(info.key==='map')startLiveMap();else if(wasLive)stopLiveMap();
}
function navigateTo(path){
  if(!routeInfo[path])return;
  if(location.pathname!==path)history.pushState({},'',path);
  activatePage(path);window.scrollTo({top:0,behavior:'auto'});document.getElementById('page-title').focus({preventScroll:true});
  if(path==='/knowledge'&&savedPoints.length)loadKnowledge();
  if(path==='/initialize')loadInitialization();
  if(path==='/navigation')loadNavigationPolicy();
}
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-nav]');
  if(!link||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  event.preventDefault();navigateTo(link.getAttribute('href'));
});
window.addEventListener('popstate',()=>{activatePage(location.pathname);if(location.pathname==='/initialize')loadInitialization();if(location.pathname==='/navigation')loadNavigationPolicy();});
document.getElementById('point-search').addEventListener('input',renderSavedPoints);
async function refreshHealth(){
  try{
    const response=await fetch('/api/health'),body=await response.json();
    if(!response.ok||body.status!=='success')throw new Error(body.message||'连接失败');
    setStatus(body.message,'ok');
    document.getElementById('home-connection').textContent='已连接 PC2';
    document.getElementById('home-omni').textContent=body.omni?'已配置':'未配置';
    document.getElementById('system-web').textContent='连接正常';
    document.getElementById('system-omni').textContent=body.omni?'已配置':'未配置';
    document.getElementById('system-assistant').textContent=body.assistant?'已启动':'未启动（可直接使用 Omni）';
    document.getElementById('system-motion').textContent=body.motion_enabled?'已开启，执行仍需确认与预检':'已关闭';
  }catch(e){
    setStatus('无法连接 PC2：'+e.message,'bad');
    document.getElementById('home-connection').textContent='连接中断';
    document.getElementById('system-web').textContent='连接中断';
  }
}
async function refreshSystem(){
  const button=document.getElementById('system-refresh');button.disabled=true;
  const box=document.getElementById('system-status');box.textContent='正在检查模块状态……';
  try{
    await refreshHealth();
    const responses=await Promise.all([fetch('/api/status'),fetch('/api/assets')]);
    const [status,assets]=await Promise.all(responses.map(response=>response.json()));
    if(responses.some(response=>!response.ok)||status.status!=='success'||assets.status!=='success')throw new Error('模块检查失败');
    const robot=status.robot;
    const items=[['system-localizer',robot.localizer],['system-planner',robot.planner],['system-controller',robot.safe_controller]];
    for(const [id,ready] of items)document.getElementById(id).textContent=ready?'节点已启动':'节点未启动';
    document.getElementById('system-localized').textContent=robot.localized?'重定位状态有效（仍需核对位姿）':'尚未成功重定位';
    document.getElementById('system-map').textContent=assets.assets.map_2d&&assets.assets.map_3d?'2D / 3D 地图文件存在':'地图文件不完整';
    document.getElementById('system-route').textContent=assets.assets.route?'示教路线文件存在':'未找到示教路线';
    document.getElementById('system-details').textContent=JSON.stringify({robot:status.robot,assets:assets.assets},null,2);
    box.textContent='检查完成 · '+new Date().toLocaleTimeString('zh-CN');
  }catch(e){box.textContent='检查失败：'+e.message;}
  finally{button.disabled=false;}
}
activatePage(location.pathname);
refreshHealth();
watchTask();
loadPoints();
syncInitializationButtons();
if(location.pathname==='/initialize')loadInitialization();
if(location.pathname==='/navigation')loadNavigationPolicy();
"""


def render_page(path):
    if path not in WEB_ROUTES:
        raise ValueError("未知页面")
    info = WEB_ROUTES[path]
    page = PAGE.replace("__PAGE_KEY__", info["key"])
    page = page.replace("__PAGE_TITLE__", escape(info["title"]))
    page = page.replace("__PAGE_DESCRIPTION__", escape(info["description"]))
    page = page.replace('data-view="{}" hidden'.format(info["key"]), 'data-view="{}"'.format(info["key"]))
    if info["key"] in ("assistant", "tasks", "agent", "navigation"):
        page = page.replace('id="plan-panel" hidden', 'id="plan-panel"')
    page = page.replace('href="{}" data-nav'.format(path), 'href="{}" data-nav aria-current="page"'.format(path))
    if info["key"] == "point_new":
        page = page.replace('href="/points" data-nav', 'href="/points" data-nav aria-current="location"')
    return page
