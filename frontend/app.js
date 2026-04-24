// xms 前端逻辑 - v3 完整版
const { createApp, ref, computed, onMounted } = Vue;
const API = '/api';

createApp({
  setup() {
    // ===== 登录状态 =====
    const loggedIn = ref(false);
    const loginTab = ref('account');  // 默认账号登录
    const token = ref(localStorage.getItem('xms_token') || '');
    const accountUsername = ref('');
    const accountPassword = ref('');
    const loggingIn = ref(false);
    const loginError = ref('');

    // 光鸭云盘登录
    const qrcode = ref('');
    const qrcodeStatus = ref('');
    const qrcodeDeviceCode = ref('');
    const phone = ref('');
    const smsCode = ref('');
    const username = ref('');
    const verificationToken = ref('');
    const stepMsg = ref('');
    const stepMsgType = ref('info');
    const smsBtnText = ref('发送验证码');
    const sendingSMS = ref(false);
    const userInfo = ref({});

    // ===== 主视图 =====
    const view = ref('dashboard');
    const fileList = ref([]);
    const loading = ref(false);
    const currentDirId = ref(null);
    const pathStack = ref([]);
    const hasMore = ref(false);
    const page = ref(0);

    // ===== STRM =====
    const syncing = ref(false);
    const syncProgress = ref(0);
    const syncProgressMsg = ref('');
    const syncConfig = ref({ parentId: '', depth: 3, deleteSync: true, refreshLinks: true });
    const syncDirs = ref([]);
    const cacheCount = ref(0);
    const logs = ref([]);
    const strmStats = ref({ count: 0, lastSync: '从未' });

    // ===== 定时任务 =====
    const tasks = ref([]);

    // ===== Webhook =====
    const webhooks = ref([]);

    // ===== 插件 =====
    const pluginTab = ref('rename');
    const pluginList = ref([]);

    // ===== 设置 =====
    const cfg = ref({ strmDir: '/app/media/strm', mediaRoot: '/app/media', tmdbKey: '' });

    // ===== 转存 =====
    const transferLink = ref('');
    const transferring = ref(false);
    const transferMsg = ref('');
    const transferTasks = ref([]);
    const transferRunning = computed(() => transferTasks.value.filter(t => t.status === 'transferring' || t.status === 'parsing').length);
    const transferDone = computed(() => transferTasks.value.filter(t => t.status === 'done' || t.status === 'failed').length);

    // ===== 预加载 =====
    const preloadEnabled = ref(true);
    const embyWebhookUrl = computed(() => window.location.origin + '/api/emby/webhook');

    // ===== CMS =====
    const subscriptions = ref([]);
    const history = ref([]);
    const cmsStats = ref({});

    // ===== TG =====
    const tgEnabled = ref(false);
    const tgToken = ref('');
    const tgAdminIds = ref('');
    const tgMsg = ref('');

    // ===== 播放器 =====
    const playerVisible = ref(false);
    const playerUrl = ref('');

    // ===== 统计数据 =====
    const stats = computed(() => ({
      strmCount: strmStats.value.count,
      fileCount: fileList.value.length,
      taskCount: tasks.value.length,
      cacheCount: cacheCount.value,
    }));

    // ===== Axios 配置 =====
    if (token.value) {
      axios.defaults.headers.common['Authorization'] = 'Bearer ' + token.value;
    }

    // ===== 账号密码登录 =====
    async function accountLogin() {
      if (!accountUsername.value || !accountPassword.value) return;
      loggingIn.value = true;
      loginError.value = '';
      try {
        const r = await axios.post(API + '/auth/login', null, {
          params: { username: accountUsername.value, password: accountPassword.value }
        });
        token.value = r.data.token;
        localStorage.setItem('xms_token', token.value);
        axios.defaults.headers.common['Authorization'] = 'Bearer ' + token.value;
        loggedIn.value = true;
        loadDashboard();
      } catch (e) {
        loginError.value = e.response?.data?.detail || '登录失败';
      }
      loggingIn.value = false;
    }

    async function logout() {
      try { await axios.post(API + '/auth/logout', null, { params: { token: token.value } }); } catch (e) {}
      token.value = '';
      localStorage.removeItem('xms_token');
      loggedIn.value = false;
      loggedIn.value = false;
    }

    // ===== 光鸭云盘登录 =====
    async function generateQRCode() {
      try {
        const r = await axios.post(API + '/guangya/qrcode/generate');
        if (r.data.qrcode_url) { qrcode.value = r.data.qrcode_url; qrcodeDeviceCode.value = r.data.device_code; pollQRCode(); }
      } catch (e) { qrcodeStatus.value = { type: 'error', msg: '生成失败' }; }
    }

    async function pollQRCode() {
      if (!qrcodeDeviceCode.value) return;
      const check = async () => {
        try {
          const r = await axios.post(API + '/guangya/qrcode/check', null, { params: { device_code: qrcodeDeviceCode.value } });
          if (r.data.access_token) { qrcodeStatus.value = { type: 'success', msg: '登录成功！' }; loadDashboard(); }
          else if (r.data.pending) { qrcodeStatus.value = { type: 'info', msg: '等待扫码...' }; setTimeout(check, 2000); }
          else { qrcodeStatus.value = { type: 'error', msg: r.data.error || '扫码超时' }; }
        } catch (e) { setTimeout(check, 3000); }
      };
      setTimeout(check, 2000);
    }

    async function sendSMS() {
      if (!phone.value || phone.value.length < 11) return;
      sendingSMS.value = true;
      try {
        const r = await axios.post(API + '/guangya/phone/send_code', null, { params: { phone: phone.value } });
        if (r.data.verification_id) { verificationToken.value = r.data.verification_token || ''; stepMsg.value = '验证码已发送'; stepMsgType.value = 'success'; }
        else { stepMsg.value = r.data.error || '发送失败'; stepMsgType.value = 'error'; }
      } catch (e) { stepMsg.value = '发送失败'; stepMsgType.value = 'error'; }
      sendingSMS.value = false;
    }

    async function signin() {
      try {
        const r = await axios.post(API + '/guangya/phone/signin', null, {
          params: { verification_code: smsCode.value, verification_token: verificationToken.value, username: username.value }
        });
        if (r.data.success) { loadDashboard(); }
        else { stepMsg.value = '登录失败'; stepMsgType.value = 'error'; }
      } catch (e) { stepMsg.value = '登录失败'; stepMsgType.value = 'error'; }
    }

    // ===== 文件 =====
    async function loadFiles(parentId) {
      loading.value = true;
      page.value = 0;
      try {
        const r = await axios.get(API + '/files', { params: { parent_id: parentId, page: 0, page_size: 50 } });
        fileList.value = r.data.data?.list || [];
        currentDirId.value = parentId;
        hasMore.value = (r.data.data?.list || []).length === 50;
        if (parentId === null) pathStack.value = [];
      } catch (e) { fileList.value = []; }
      loading.value = false;
    }

    function enterDir(item) {
      pathStack.value.push({ id: currentDirId.value, name: item.fileName || item.name });
      loadFiles(item.fileId || item.id);
    }

    function goBackDir() {
      if (!pathStack.value.length) return;
      const prev = pathStack.value.pop();
      loadFiles(prev.id);
    }

    function jumpToPath(index) {
      pathStack.value = pathStack.value.slice(0, index);
      loadFiles(index === 0 ? null : pathStack.value[index - 1]?.id);
    }

    async function loadMore() {
      page.value++;
      try {
        const r = await axios.get(API + '/files', { params: { parent_id: currentDirId.value, page: page.value, page_size: 50 } });
        const list = r.data.data?.list || [];
        fileList.value.push(...list);
        hasMore.value = list.length === 50;
      } catch (e) {}
    }

    function getIcon(item) {
      const n = (item.fileName || item.name || '').toLowerCase();
      if (n.endsWith('.mp4') || n.endsWith('.mkv') || n.endsWith('.avi') || n.endsWith('.mov') || n.endsWith('.ts')) return '🎬';
      if (n.endsWith('.jpg') || n.endsWith('.png') || n.endsWith('.gif') || n.endsWith('.webp')) return '🖼️';
      return '📄';
    }

    function formatSize(b) {
      if (!b) return '';
      const u = ['B', 'KB', 'MB', 'GB'];
      let i = 0;
      while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
      return b.toFixed(1) + ' ' + u[i];
    }

    function previewFile(item) { playerUrl.value = '/embed/' + (item.fileId || item.id); playerVisible.value = true; }

    async function genSTRM(item) {
      try {
        await axios.post(API + '/strm/refresh', null, { params: { file_id: item.fileId || item.id, file_path: item.fileName || item.name } });
        addLog('success', '已生成STRM: ' + (item.fileName || item.name));
      } catch (e) { addLog('error', 'STRM生成失败'); }
    }

    // ===== STRM =====
    async function startSync() {
      syncing.value = true;
      syncProgress.value = 10;
      syncProgressMsg.value = '开始同步...';
      addLog('info', 'STRM 同步开始');
      try {
        const r = await axios.post(API + '/strm/sync', null, {
          params: { parent_id: syncConfig.value.parentId || null, depth: parseInt(syncConfig.value.depth) }
        });
        syncProgress.value = 100;
        syncProgressMsg.value = '同步完成';
        addLog('success', '同步完成: ' + (r.data.success || 0) + ' 文件');
        await loadStrmStatus();
      } catch (e) { addLog('error', '同步失败'); }
      syncing.value = false;
      setTimeout(() => { syncProgress.value = 0; }, 2000);
    }

    async function loadStrmStatus() {
      try {
        const r = await axios.get(API + '/strm/status');
        strmStats.value = r.data;
        cacheCount.value = r.data.cache_count || 0;
      } catch (e) {}
    }

    async function clearCache() {
      await axios.post(API + '/cache/clear');
      cacheCount.value = 0;
      addLog('success', '缓存已清空');
    }

    function startSyncNow() { view.value = 'strm'; startSync(); }

    // ===== 转存 =====
    async function loadTransferTasks() {
      try {
        const r = await axios.get(API + '/transfer/tasks');
        transferTasks.value = r.data.tasks || [];
      } catch (e) { transferTasks.value = []; }
    }

    async function createTransfer() {
      if (!transferLink.value) return;
      transferring.value = true;
      transferMsg.value = '';
      try {
        const r = await axios.post(API + '/transfer/create', null, { params: { link: transferLink.value } });
        transferMsg.value = '转存任务已创建: ' + r.data.task_id;
        addLog('success', '创建转存任务: ' + transferLink.value.substring(0, 50));
        transferLink.value = '';
        await loadTransferTasks();
      } catch (e) {
        transferMsg.value = '创建失败: ' + (e.response?.data?.detail || e.message);
        addLog('error', '转存任务创建失败');
      }
      transferring.value = false;
    }

    // ===== 预加载 =====
    async function rebuildPreload() {
      try {
        await axios.post(API + '/preload/rebuild');
        addLog('success', '预加载索引已重建');
      } catch (e) { addLog('error', '重建失败'); }
    }

    // ===== CMS =====
    async function loadCmsStats() {
      try {
        const r = await axios.get(API + '/cms/stats');
        cmsStats.value = r.data;
      } catch (e) {}
    }

    async function loadSubscriptions() {
      try {
        const r = await axios.get(API + '/cms/subscriptions');
        subscriptions.value = r.data.subscriptions || [];
      } catch (e) { subscriptions.value = []; }
    }

    async function loadHistory() {
      try {
        const r = await axios.get(API + '/cms/history');
        history.value = r.data.history || [];
      } catch (e) { history.value = []; }
    }

    async function addSubscription() {
      const title = prompt('输入媒体名称：');
      if (!title) return;
      const type = confirm('是剧集吗？') ? 'series' : 'movie';
      try {
        await axios.post(API + '/cms/subscriptions', null, { params: { title, media_type: type, season: 1 } });
        await loadSubscriptions();
        addLog('success', '已添加订阅: ' + title);
      } catch (e) { addLog('error', '添加订阅失败'); }
    }

    async function delSub(id) {
      try {
        await axios.delete(API + '/cms/subscriptions/' + id);
        await loadSubscriptions();
      } catch (e) {}
    }

    function openAddSub() { addSubscription(); }

    // ===== TG =====
    async function loadTGConfig() {
      try {
        const r = await axios.get(API + '/tg/config');
        tgEnabled.value = r.data.enabled;
      } catch (e) {}
    }

    async function saveTG() {
      tgMsg.value = '';
      try {
        const ids = tgAdminIds.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
        await axios.post(API + '/tg/config', null, {
          params: { enabled: tgEnabled.value, token: tgToken.value, admin_ids: ids }
        });
        tgMsg.value = '配置已保存';
        addLog('success', 'TG机器人配置已更新');
      } catch (e) {
        tgMsg.value = '保存失败';
        addLog('error', 'TG配置保存失败');
      }
    }

    // ===== 定时任务 =====
    async function loadTasks() {
      try {
        const r = await axios.get(API + '/scheduler/tasks');
        tasks.value = r.data.tasks || [];
      } catch (e) { tasks.value = []; }
    }

    async function toggleTask(task) {
      await axios.post(API + '/scheduler/tasks/' + task.id + '/toggle', null, { params: { enabled: task.enabled } });
    }

    async function runTaskNow(task) {
      await axios.post(API + '/scheduler/tasks/' + task.id + '/run');
      addLog('info', '任务 "' + task.name + '" 已触发');
    }

    async function deleteTask(task) {
      await axios.delete(API + '/scheduler/tasks/' + task.id);
      tasks.value = tasks.value.filter(t => t.id !== task.id);
    }

    function openAddTask() {
      const name = prompt('任务名称：');
      if (!name) return;
      const cron = prompt('Crontab 表达式（如：0 3 * * *）：', '0 3 * * *');
      if (!cron) return;
      axios.post(API + '/scheduler/tasks', null, { params: { name, parent_id: null, folder_path: '', cron, depth: 3 } })
        .then(() => loadTasks()).catch(() => addLog('error', '添加任务失败'));
    }

    // ===== Webhook =====
    async function loadWebhooks() {
      try {
        const r = await axios.get(API + '/webhook/list');
        webhooks.value = r.data.webhooks || [];
      } catch (e) { webhooks.value = []; }
    }

    async function deleteWebhook(wh) {
      await axios.delete(API + '/webhook/' + wh.id);
      webhooks.value = webhooks.value.filter(w => w.id !== wh.id);
    }

    function openAddWebhook() {
      const name = prompt('名称：');
      if (!name) return;
      const url = prompt('URL：');
      if (!url) return;
      axios.post(API + '/webhook', null, { params: { name, url, events: [] } })
        .then(() => loadWebhooks()).catch(() => addLog('error', '添加失败'));
    }

    // ===== 插件 =====
    async function loadPlugins() {
      try {
        const r = await axios.get(API + '/plugin/list');
        pluginList.value = r.data.plugins || [];
      } catch (e) { pluginList.value = []; }
    }

    async function deletePlugin(pl) {
      await axios.delete(API + '/plugin/' + pl.id);
      pluginList.value = pluginList.value.filter(p => p.id !== pl.id);
    }

    function openAddPlugin() {}

    const pluginListFiltered = computed(() => pluginList.value.filter(p => p.type === pluginTab.value));

    // ===== 设置 =====
    async function loadConfig() {
      try {
        const r = await axios.get(API + '/config');
        cfg.value = { ...cfg.value, ...r.data };
      } catch (e) {}
    }

    async function saveSettings() {
      await axios.post(API + '/config', null, {
        params: { username: cfg.value.username, password: cfg.value.password, strm_dir: cfg.value.strmDir, tmdb_key: cfg.value.tmdbKey }
      });
      addLog('success', '设置已保存');
    }

    // ===== 日志 =====
    function addLog(level, msg) {
      const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      logs.value.push({ time: t, level, msg });
      if (logs.value.length > 100) logs.value.shift();
    }

    function formatTime(ts) {
      if (!ts) return '-';
      return new Date(ts * 1000).toLocaleString('zh-CN');
    }

    // ===== Dashboard =====
    async function loadDashboard() {
      await Promise.all([loadStrmStatus(), loadTasks(), loadWebhooks(), loadPlugins(), loadConfig(), loadTGConfig(), loadTransferTasks(), loadCmsStats(), loadSubscriptions(), loadHistory()]);
    }

    // ===== 初始化 =====
    onMounted(async () => {
      if (token.value) {
        try {
          const r = await axios.get(API + '/auth/me');
          loggedIn.value = true;
          loadDashboard();
        } catch (e) {
          token.value = '';
          localStorage.removeItem('xms_token');
        }
      }
      if (!loggedIn.value && loginTab.value === 'qrcode') { generateQRCode(); }
    });

    return {
      // 登录
      loggedIn, loginTab, token, accountUsername, accountPassword, loggingIn, loginError,
      qrcode, qrcodeStatus, phone, smsCode, username, verificationToken,
      stepMsg, stepMsgType, smsBtnText, sendingSMS, userInfo,
      accountLogin, logout, generateQRCode, sendSMS, signin,
      // 视图
      view, fileList, loading, currentDirId, pathStack, hasMore, page,
      // 文件
      loadFiles, enterDir, goBackDir, jumpToPath, loadMore, getIcon, formatSize, previewFile, genSTRM,
      // STRM
      syncing, syncProgress, syncProgressMsg, syncConfig, cacheCount, logs, strmStats,
      startSync, loadStrmStatus, clearCache, startSyncNow,
      // 转存
      transferLink, transferring, transferMsg, transferTasks, transferRunning, transferDone,
      createTransfer, loadTransferTasks,
      // 预加载
      preloadEnabled, embyWebhookUrl, rebuildPreload,
      // CMS
      subscriptions, history, cmsStats, loadSubscriptions, loadHistory, loadCmsStats, openAddSub, delSub,
      // TG
      tgEnabled, tgToken, tgAdminIds, tgMsg, loadTGConfig, saveTG,
      // 定时
      tasks, loadTasks, toggleTask, runTaskNow, deleteTask, openAddTask,
      // Webhook
      webhooks, loadWebhooks, deleteWebhook, openAddWebhook,
      // 插件
      pluginTab, pluginList: pluginListFiltered, loadPlugins, deletePlugin, openAddPlugin,
      // 设置
      cfg, loadConfig, saveSettings,
      // 工具
      addLog, formatTime, stats,
      // 播放器
      playerVisible, playerUrl,
    };
  }
}).mount('#app');
