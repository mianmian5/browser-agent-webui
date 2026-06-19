#!/usr/bin/env python3
"""Browser Agent v4 — with multi-source search, zh.wikipedia, display stability"""
import os, base64, json, re, time
from playwright.async_api import async_playwright
import sys, httpx, re as _re, json as _json
from bs4 import BeautifulSoup
from urllib.parse import quote

# ====== Web Search (multi-source, no browser needed) ======
_SEARCH_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

def _remove_unwanted_tags(soup):
    for t in soup(["script","style","nav","footer","header","aside","noscript","iframe","svg"]):
        t.decompose()
    return soup

def _fetch_page_content(url, max_len=3000):
    """Fetch and extract readable text from a URL"""
    try:
        resp = httpx.get(url, headers={"User-Agent": _SEARCH_UA}, timeout=10, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        _remove_unwanted_tags(soup)
        # Try main content selectors first
        for sel in ["article", "[role=main]", ".mw-parser-output", ".post-content", "#mw-content-text", "main", ".content"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text[:max_len]
        return soup.get_text(separator="\n", strip=True)[:max_len]
    except:
        return ""

def _bing_search(query, max_results=4):
    try:
        r = httpx.get("https://www.bing.com/search", params={"q": query, "setlang": "zh-Hans"},
            headers={"User-Agent": _SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=10, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for el in soup.select(".b_algo")[:max_results]:
            title_el = el.select_one("h2 a")
            snippet_el = el.select_one(".b_caption p")
            if title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": title_el.get("href",""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else ""
                })
        return results
    except:
        return []

def _duckduckgo_search(query, max_results=4):
    """DuckDuckGo search (no API key needed, works in China)"""
    try:
        r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
            headers={"User-Agent": _SEARCH_UA},
            timeout=10, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for el in soup.select(".result")[:max_results]:
            title_el = el.select_one(".result__title a")
            snippet_el = el.select_one(".result__snippet")
            if title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": title_el.get("href",""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else ""
                })
        return results
    except:
        return []

def _baidu_search(query, max_results=4):
    """Baidu search backup"""
    try:
        r = httpx.get("https://www.baidu.com/s", params={"wd": query, "ie": "utf-8"},
            headers={"User-Agent": _SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=10, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for el in soup.select(".result")[:max_results]:
            title_el = el.select_one("h3 a")
            snippet_el = el.select_one(".c-abstract") or el.select_one(".content-right_8Zs40")
            if title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": title_el.get("href",""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else ""
                })
        return results
    except:
        return []

SEARCH_ENGINES = [
    ("Bing", _bing_search),
    ("DuckDuckGo", _duckduckgo_search),
    ("百度", _baidu_search),
]

def search_web(query):
    """Multi-engine web search. Returns results from the first engine that returns something."""
    formatted = f"📌 搜索: {query}\n"
    attempted = []

    for name, engine in SEARCH_ENGINES:
        try:
            results = engine(query)
            attempted.append(name)
            if results:
                formatted += f"  [{name}] 找到 {len(results)} 条结果:\n"
                for i, r in enumerate(results, 1):
                    formatted += f"{i}. {r['title']}\n"
                    if r.get('snippet'):
                        formatted += f"   {r['snippet']}\n"
                    formatted += f"   {r['url']}\n"
                    # Fetch content from first result (skip search engines themselves)
                    if i == 1 and r.get("url","") and "bing.com" not in r["url"] and "duckduckgo.com" not in r["url"]:
                        content = _fetch_page_content(r["url"])
                        if content:
                            formatted += f"  详细内容: {content[:2000]}\n"
                return formatted
        except:
            pass

    # If all fail, try direct knowledge sources
    # Try Chinese Wikipedia
    zh_title = query.replace(" ", "_")
    zh_url = f"https://zh.wikipedia.org/wiki/{quote(query)}"
    try:
        content = _fetch_page_content(zh_url)
        if content and len(content) > 100:
            return f"📌 来自维基百科（中文）:\nURL: {zh_url}\n\n{content[:3000]}"
    except:
        pass

    # Try Baidu Baike
    baike_url = f"https://baike.baidu.com/item/{quote(query)}"
    try:
        content = _fetch_page_content(baike_url)
        if content and len(content) > 100:
            return f"📌 来自百度百科:\nURL: {baike_url}\n\n{content[:3000]}"
    except:
        pass

    return f"搜索无结果（已尝试: {', '.join(attempted) if attempted else '无搜索引擎可用'}）"


# ====== Prompt ======
PROMPT_T = """You are a browser automation agent. Complete the user task.

Tools:
- search(query) - Search the web (recommended FIRST step for finding info)
- navigate(url) - Go to a URL
- click(selector) - Click element
- type(selector, text) - Type text into input
- scroll(direction) - Scroll page (up/down)
- wait(ms) - Wait milliseconds
- extract(selector) - Get text content (default: "body" for whole page)
- done(answer) - Task complete with final answer

Rules:
1. Always think in Chinese
2. When user asks a factual question → FIRST use search(query) - it returns real content from multiple search engines
3. search() already fetches the FULL page content for you - you can answer directly from it
4. After getting search results, use done() to present the answer - don't keep searching
5. Wikipedia is blocked in China. Use search() to find info instead of navigating to wikipedia.org
6. If you navigate to a page and see "Bad Request" or error, use search() instead
7. If an action repeats >=3 times, done() with what you have
8. For lists (like "历年获奖者"), search() may need a specific query - try different phrasing
9. After search() gives results, answer from them. Do NOT click on search result links.

Current: __URL__ | Title: __TITLE__
History:
__HISTORY__

Task: __TASK__

Reply ONLY this JSON (no markdown):
{"thought":"你的思考","action":"search/navigate/click/type/scroll/wait/extract/done","params":{}}
"""


async def call_llm(api_key, base_url, model, prompt):
    import httpx
    hdrs = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.1, "max_tokens": 2048}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(base_url.rstrip("/") + "/chat/completions", headers=hdrs, json=payload)
            if r.status_code != 200:
                return '{"thought":"API错误","action":"done","params":{"answer":"API ' + str(r.status_code) + '"}}'
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return '{"thought":"API连接失败","action":"done","params":{"answer":"' + str(e)[:200] + '"}}'


def parse_json(text):
    if not text: return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try: return json.loads(text)
    except: pass
    for i in range(len(text)):
        if text[i] == "{":
            d = 0
            for j in range(i, len(text)):
                if text[j] == "{": d += 1
                elif text[j] == "}": d -= 1
                if d == 0:
                    try: return json.loads(text[i:j+1])
                    except: continue
            break
    return None


class BrowserAgent:
    def __init__(self, llm_api_key, llm_base_url="https://api.openai.com/v1",
                 llm_model="gpt-4o", headless=True):
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.headless = headless
        self.browser = None
        self.page = None
        self.playwright = None
        self.history = []
        self.initial_task = ""
        self.repeat_count = 0
        self.last_action_key = ""
        self.stopped = False

    def set_task(self, task):
        self.initial_task = task

    def stop(self):
        self.stopped = True

    async def _cleanup(self):
        if self.browser:
            try: await self.browser.close()
            except: pass
            self.browser = None
        if self.playwright:
            try: await self.playwright.stop()
            except: pass
            self.playwright = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-gpu", "--disable-software-rasterizer"])
        ctx = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=_SEARCH_UA)
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.page = await ctx.new_page()
        await self.page.goto("about:blank")
        return self

    async def screenshot(self):
        try:
            return base64.b64encode(await self.page.screenshot(type="png", full_page=False)).decode()
        except:
            return ""

    def _is_looping(self, action, url):
        key = f"{action}:{url}"
        if key == self.last_action_key:
            self.repeat_count += 1
        else:
            self.repeat_count = 0
            self.last_action_key = key
        return self.repeat_count >= 3

    async def run_step(self, task):
        if self.stopped:
            await self._cleanup()
            return {"type": "action", "thought": "用户已停止", "action": "done",
                    "observation": "任务已取消", "done": True}

        if not task: task = self.initial_task
        try:
            url = self.page.url
            title = await self.page.title()
        except:
            return {"type": "error", "content": "浏览器状态异常"}

        # Bad Request detection -> use search instead of navigating
        if "Bad Request" in title or "400" in url or "error" in title.lower():
            # Use search() as fallback
            search_result = search_web(task[:100])
            return {"type": "action",
                    "thought": "页面出错，改用搜索获取信息",
                    "action": "search",
                    "observation": search_result[:2000],
                    "done": False}

        hist_lines = [f"[{h.get('action','?')}] {h.get('result','')[:200]}"
                      for h in self.history[-8:]]
        prompt = PROMPT_T
        for a, b in [("__URL__", url), ("__TITLE__", title),
                     ("__HISTORY__", "\n".join(hist_lines)), ("__TASK__", task)]:
            prompt = prompt.replace(a, b)

        result = await call_llm(self.llm_api_key, self.llm_base_url, self.llm_model, prompt)
        ad = parse_json(result)

        if not ad:
            return {"type": "error", "content": "LLM解析错误: " + result[:200]}

        action = ad.get("action", "")
        params = ad.get("params", {})
        thought = ad.get("thought", "")
        obs = ""

        if self.stopped:
            obs = "用户已停止"
            action, params = "done", {"answer": obs}
        elif self._is_looping(action, url):
            obs = "检测到重复操作，自动完成"
            action, params = "done", {"answer": f"已尽力完成: {self.initial_task[:50]}"}

        try:
            if action == "navigate":
                t = params.get("url", "about:blank")
                try:
                    await self.page.goto(t, wait_until="domcontentloaded", timeout=20000)
                    await self.page.wait_for_timeout(1000)
                except:
                    pass
                obs = "已导航到: " + self.page.url

            elif action == "click":
                s = params.get("selector", "")
                for sel in [s, "input[type=submit]", "[type=submit]", "button", "a"]:
                    try:
                        loc = self.page.locator(sel)
                        if await loc.count() > 0:
                            await loc.first.click(timeout=3000)
                            await self.page.wait_for_timeout(1500)
                            obs = "已点击: " + sel
                            break
                    except: continue
                if not obs: obs = "点击失败"

            elif action == "type":
                t = params.get("text", "")
                tried = False
                for sel in ["input[name=q]", "#sb_form_q", "input[type=text]", "textarea"]:
                    try:
                        loc = self.page.locator(sel)
                        if await loc.count() > 0:
                            await loc.first.fill(t, timeout=3000)
                            await self.page.keyboard.press("Enter")
                            await self.page.wait_for_timeout(1500)
                            tried = True
                            obs = "已输入: " + t[:80]
                            break
                    except: continue
                if not tried: obs = "输入失败"

            elif action == "scroll":
                d = params.get("direction", "down")
                await self.page.evaluate(
                    f"window.scrollBy(0, {'window.innerHeight' if d=='down' else '-window.innerHeight'})")
                await self.page.wait_for_timeout(500)
                obs = "已滚动: " + d

            elif action == "wait":
                ms = min(params.get("ms", 1000), 5000)
                await self.page.wait_for_timeout(ms)
                obs = "已等待: " + str(ms) + "ms"

            elif action == "extract":
                sel = params.get("selector", "body")
                try:
                    text = await self.page.evaluate(
                        f"document.querySelector({json.dumps(sel)})?.innerText || ''")
                    if not text:
                        text = await self.page.evaluate("document.body.innerText")
                    obs = "页面内容: " + text[:2000]
                except:
                    obs = "提取失败"

            elif action == "done":
                obs = params.get("answer", "任务完成")

            elif action == "search":
                q = params.get("query", "") or params.get("text", "") or params.get("url", "")
                if not q: q = task[:100]
                obs = search_web(q)[:3000]

            elif action in ("go", "open", "goto"):
                u = params.get("url", "") or params.get("query", "") or params.get("text", "")
                if u:
                    if not u.startswith("http"):
                        u = "https://zh.wikipedia.org/wiki/" + u.replace(" ", "_")
                    try:
                        await self.page.goto(u, wait_until="domcontentloaded", timeout=15000)
                        await self.page.wait_for_timeout(1000)
                    except:
                        pass
                    obs = "已导航到: " + self.page.url
                else:
                    obs = "未知动作: " + action

            else:
                obs = "未知动作: " + action

        except Exception as e:
            obs = "执行出错: " + str(e)[:100]

        self.history.append({"action": action, "thought": thought,
                            "params": params, "result": obs})
        return {"type": "action", "thought": thought, "action": action,
                "observation": obs, "done": action == "done"}
