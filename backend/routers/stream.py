"""
302 流播放路由 - xms
性能优化版：连接池 + URL缓存 + 预获取
"""
from fastapi import APIRouter, HTTPException, Response, Query
from fastapi.responses import RedirectResponse
import httpx
from ..main import get_client, get_strm_service
from ..stream_cache import stream_cache

router = APIRouter()

# 共享 HTTP 客户端（连接池复用）
_http_client: httpx.AsyncClient = None


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            follow_redirects=False,  # 我们自己处理重定向
        )
    return _http_client


@router.get("/stream/{file_id}")
async def stream_redirect(file_id: str, prefetch: bool = Query(False)):
    """
    302 重定向核心端点 - 极致优化
    1. 先查缓存，有则直接返回 302
    2. 缓存未命中则并发获取：查缓存 + 查直链
    3. prefetch=true 时预热附近文件直链
    """
    client = get_client()
    if not client.access_token:
        raise HTTPException(status_code=401, detail="未登录光鸭云盘")

    async def _fetch_url(fid: str) -> str:
        url = client.get_stream_url(fid)
        return url if url else ""

    # 缓存优先
    cached = stream_cache.url_cache.get(file_id)
    if cached:
        return RedirectResponse(url=cached, status_code=302)

    # 缓存未命中，获取直链
    url = await stream_cache.get_url(file_id, _fetch_url, ttl=300)
    if not url:
        raise HTTPException(status_code=404, detail="获取直链失败")

    return RedirectResponse(url=url, status_code=302)


@router.get("/stream/direct/{file_id}")
async def stream_direct(file_id: str):
    """
    直接代理模式 - 带宽换稳定性
    不做 302，直接代理流数据
    适合直链不稳定或被拦截的场景
    """
    client = get_client()
    if not client.access_token:
        raise HTTPException(status_code=401, detail="未登录光鸭云盘")

    url = await stream_cache.get_url(file_id, lambda fid: client.get_stream_url(fid), ttl=300)
    if not url:
        raise HTTPException(status_code=404, detail="获取直链失败")

    http = await get_http_client()
    try:
        resp = await http.get(url, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "video/mp4"),
            headers={
                "Content-Disposition": resp.headers.get("content-disposition", ""),
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=300",
            }
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"代理失败: {e}")


@router.get("/stream/prefetch")
async def stream_prefetch(file_ids: str = Query(..., description="逗号分隔的file_id列表")):
    """
    预获取直链到缓存
    播放列表播放下一集时，预先抓取直链
    """
    client = get_client()
    if not client.access_token:
        raise HTTPException(status_code=401, detail="未登录光鸭云盘")

    file_list = [f.strip() for f in file_ids.split(",") if f.strip()]
    if not file_list:
        raise HTTPException(status_code=400, detail="file_ids 不能为空")

    async def _fetch(fid: str) -> str:
        try:
            return client.get_stream_url(fid) or ""
        except Exception:
            return ""

    stream_cache.prefetch(file_list, _fetch)
    return {"status": "ok", "prefetching": len(file_list)}


@router.get("/embed/{file_id}")
async def embed_player(file_id: str):
    """内嵌播放页"""
    client = get_client()
    if not client.access_token:
        raise HTTPException(status_code=401, detail="未登录光鸭云盘")

    url = await stream_cache.get_url(file_id, lambda fid: client.get_stream_url(fid), ttl=300)
    if not url:
        raise HTTPException(status_code=404, detail="获取直链失败")

    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>xms Player</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:#000; display:flex; justify-content:center; align-items:center; height:100vh; overflow:hidden; }}
    video {{ max-width:100%; max-height:100%; }}
  </style>
</head>
<body>
  <video id="v" controls playsinline autoplay>
    <source src="/stream/direct/{file_id}" type="video/mp4">
  </video>
  <script>
    // 预取下一段进度
    const v = document.getElementById('v');
    v.addEventListener('timeupdate', () => {{
      if (v.duration && v.currentTime > v.duration * 0.8) {{
        // 通知服务端预热（静默）
        fetch('/stream/prefetch?file_ids={file_id}').catch(() => {{}});
      }}
    }});
  </script>
</body>
</html>"""
    return Response(content=html, media_type="text/html")


@router.get("/cache/stats")
async def cache_stats():
    """缓存状态"""
    return {
        "cached_count": len(stream_cache.url_cache),
        "max_size": stream_cache.url_cache.maxsize,
    }


@router.post("/cache/clear")
async def cache_clear():
    """清空缓存"""
    stream_cache.clear()
    return {"status": "ok"}
