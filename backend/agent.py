#!/usr/bin/env python3
"""Browser Agent v3 - with loop detection, Wikipedia fallback, stop support"""
import os, base64, json, re, time
from playwright.async_api import async_playwright
import sys, httpx, re as _re, json as _json
from bs4 import BeautifulSoup

# ---------- API Search (no browser needed) ----------
_SEARCH_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

def _bing_search(query, max_results=3):
    try:
        r = httpx.get("https://www.bing.com/search", params={"q": query, "setlang": "zh-Hans"},
            headers={"User-Agent": _SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=10, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for el in soup.select(".b_algo")[:max_results]:
            title = el.select_one("h2 a")
            snippet = el.select_one(".b_caption p")
            if title:
                results.append({"title": title.get_text(strip=True), "url": title.get("href",""),
                    "snippet": snippet.get_text(strip=True) if snippet else ""})
        return results
    except Exception as e:
        return []

def search_web(task_text):
    """Real web search using Bing API (no browser needed)"""
    results = _bing_search(task_text[:60], 3)
    if not results:
        return "搜索无结果"
    parts = [f"搜索结果: {task_text}"]
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r[chr(116)+chr(105)+chr(116)+chr(108)+chr(101)]}")
        parts.append(f"   {r[chr(115)+chr(110)+chr(105)+chr(112)+chr(112)+chr(101)+chr(116)]}")
        parts.append(f"   {r[chr(117)+chr(114)+chr(108)]}")
        # Fetch content of first result
        if i == 1 and r.get("url","") and "bing.com" not in r["url"]:
            try:
                resp = httpx.get(r["url"], headers={"User-Agent": _SEARCH_UA}, timeout=8)
                soup = BeautifulSoup(resp.text, "html.parser")
                for t in soup(["script","style","nav","footer"]): t.decompose()
                text = soup.get_text(separator="\n", strip=True)[:3000]
                parts.append(f"\n详细内容:\n{text}")
            except: pass
    return "\n".join(parts)

PROMPT_T = """You are a browser automation agent. Complete the user task.

Tools:
- navigate(url) - Go to a URL
- search(query) - Search the web for information (use this instead of navigate for finding info)
- click(selector) - Click element
- type(selector, text) - Type text
- scroll(direction) - Scroll
- wait(ms) - Wait
- extract(selector) - Get text from page
- done(answer) - Task complete

Rules:
1. Use Chinese for thoughts
2. For factual questions, use search(query) - it returns real content
3. After search() gives you results, use done() to answer with those results
4. Do NOT navigate/click on search result links (they require login or have anti-bot)
5. The search() results already contain the information you need (en.wikipedia.org)
3. If a page shows "Bad Request" or error, try Wikipedia instead
4. After navigating, use extract("body") to read the page content
5. Summarize from what you have - do NOT keep searching for perfect results
6. If same action repeated >=3 times, mark done with what you have
7. Execute one action at a time. Use done() when you have enough info.

Current: __URL__ | Title: __TITLE__
History:
__HISTORY__

Task: __TASK__

Reply ONLY this JSON:
{"thought":"思考","action":"navigate/click/type/scroll/wait/extract/done","params":{}}
"""

async def call_llm(api_key, base_url, model, prompt):
    import httpx
    hdrs = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 2048}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(base_url.rstrip("/") + "/chat/completions", headers=hdrs, json=payload)
            if r.status_code != 200:
                return '{"thought":"API err","action":"done","params":{"answer":"API ' + str(r.status_code) + '"}}'
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return '{"thought":"API fail","action":"done","params":{"answer":"' + str(e)[:200] + '"}}'


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
    def __init__(self, llm_api_key, llm_base_url="https://api.openai.com/v1", llm_model="gpt-4o", headless=True):
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
                  "--disable-blink-features=AutomationControlled"])
        ctx = await self.browser.new_context(viewport={"width": 1280, "height": 800})
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

    async def _try_wikipedia_fallback(self, task):
        """When Bing/other returns Bad Request, try Wikipedia"""
        q = task.replace("搜索", "").replace("帮我", "").replace(" ", "").strip()
        if not q: q = task[:30]
        wiki_url = "https://en.wikipedia.org/wiki/" + q.replace(" ", "_")
        try:
            await self.page.goto(wiki_url, wait_until="domcontentloaded", timeout=15000)
            await self.page.wait_for_timeout(2000)
            text = await self.page.evaluate("document.body.innerText")
            preview = text[:3000] if text else ""
            return wiki_url, preview
        except:
            return None, None

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
            return {"type": "error", "content": "Browser error"}

        # Bad Request detection -> Wikipedia fallback
        if "Bad Request" in title or "400" in url or "error" in title.lower():
            wiki_url, wiki_text = await self._try_wikipedia_fallback(task)
            if wiki_url:
                obs = f"已改用 Wikipedia: {wiki_url}"
                self.history.append({"action": "navigate", "thought": "Fallback to Wikipedia",
                                     "params": {"url": wiki_url}, "result": obs})
                return {"type": "action", "thought": "Bing被屏蔽，已切换到Wikipedia",
                        "action": "navigate", "observation": obs, "done": False}

        # Build history prompt
        hist_lines = [f"[{h.get('action','?')}] {h.get('result','')[:200]}"
                      for h in self.history[-8:]]
        prompt = PROMPT_T
        for a, b in [("__URL__", url), ("__TITLE__", title),
                     ("__HISTORY__", "\n".join(hist_lines)), ("__TASK__", task)]:
            prompt = prompt.replace(a, b)

        result = await call_llm(self.llm_api_key, self.llm_base_url, self.llm_model, prompt)
        ad = parse_json(result)

        if not ad:
            return {"type": "error", "content": "LLM parse error: " + result[:200]}

        action = ad.get("action", "")
        params = ad.get("params", {})
        thought = ad.get("thought", "")
        obs = ""

        # Loop detection + stop check
        if self.stopped:
            obs = "用户已停止"
            action, params = "done", {"answer": obs}
        elif self._is_looping(action, url):
            obs = "检测到重复操作，自动完成当前任务"
            action, params = "done", {"answer": f"已尽力完成: {self.initial_task[:50]}"}

        try:
            if action == "navigate":
                t = params.get("url", "about:blank")
                await self.page.goto(t, wait_until="domcontentloaded", timeout=20000)
                await self.page.wait_for_timeout(2000)
                obs = "已导航到: " + self.page.url

            elif action == "click":
                s = params.get("selector", "")
                for sel in [s, "input[type=submit]", "[type=submit]", "button", "a"]:
                    try:
                        loc = self.page.locator(sel)
                        if await loc.count() > 0:
                            await loc.first.click(timeout=3000)
                            await self.page.wait_for_timeout(2000)
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
                            await self.page.wait_for_timeout(2000)
                            tried = True
                            obs = "已输入: " + t[:80]
                            break
                    except: continue
                if not tried: obs = "输入失败"

            elif action == "scroll":
                d = params.get("direction", "down")
                if d == "down": await self.page.evaluate("window.scrollBy(0, window.innerHeight)")
                else: await self.page.evaluate("window.scrollBy(0, -window.innerHeight)")
                obs = "已滚动: " + d

            elif action == "wait":
                ms = min(params.get("ms", 1000), 5000)
                await self.page.wait_for_timeout(ms)
                obs = "已等待: " + str(ms) + "ms"

            elif action == "extract":
                try:
                    text = await self.page.evaluate("document.body.innerText")
                    obs = "页面内容: " + text[:1500]
                except:
                    obs = "提取失败"

            elif action == "done":
                obs = params.get("answer", "任务完成")

            elif action == "search":
                # API-based search (no browser needed)
                q = params.get("query", "") or params.get("text", "") or params.get("url", "")
                if not q: q = task[:60]
                search_result = search_web(q)
                obs = search_result[:2000]
                # Also create a log entry
                self.history.append({"action": "search_result", "thought": "搜索完成", "params": {}, "result": obs})

            elif action in ("go", "open", "goto"):
                u = params.get("url", "") or params.get("query", "") or params.get("text", "")
                if u:
                    if not u.startswith("http"):
                        u = "https://en.wikipedia.org/wiki/" + u.replace(" ", "_")
                    await self.page.goto(u, wait_until="domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(1000)
                    obs = "已导航到: " + self.page.url
                else: obs = "未知动作: " + action

            else:
                obs = "未知动作: " + action

        except Exception as e:
            obs = "执行出错: " + str(e)[:100]

        self.history.append({"action": action, "thought": thought,
                            "params": params, "result": obs})
        return {"type": "action", "thought": thought, "action": action,
                "observation": obs, "done": action == "done"}
