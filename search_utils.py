import logging
import aiohttp
import re
import warnings
from urllib.parse import urlparse, unquote

# Игнорируем предупреждения
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Запрещённые слова для поиска
FORBIDDEN_WORDS = [
    'порно', 'porn', 'xxx', 'секс', 'sex', 'эротика', 'erotica',
    'гей', 'gay', 'лесби', 'lesbian', 'транс', 'trans',
    'инцест', 'incest', 'педофилия', 'pedophilia', 'зоофилия', 'bestiality',
    'наркотики', 'drugs', 'кокаин', 'cocaine', 'героин', 'heroin',
    'оружие', 'weapon', 'огнестрельное', 'firearm', 'взрывчатка', 'explosive',
    'бомба', 'bomb', 'терроризм', 'terrorism', 'экстремизм', 'extremism',
    'убийство', 'murder', 'суицид', 'suicide', 'самоубийство'
]

def is_forbidden_query(query):
    """Проверяет, является ли запрос запрещённым"""
    query_lower = query.lower()
    for word in FORBIDDEN_WORDS:
        if word in query_lower:
            return True
    return False

def clean_duckduckgo_url(url):
    """Очищает ссылку от редиректа DuckDuckGo"""
    if not url:
        return url
    
    if '//duckduckgo.com/l/' in url or 'duckduckgo.com/l/' in url:
        match = re.search(r'uddg=([^&]+)', url)
        if match:
            decoded = unquote(match.group(1))
            return decoded
    
    return url

# Пытаемся импортировать DDGS
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
    print("✅ DuckDuckGo поиск доступен")
except ImportError:
    try:
        from ddgs import DDGS
        DDGS_AVAILABLE = True
        print("✅ DDGS поиск доступен")
    except ImportError:
        DDGS_AVAILABLE = False
        print("⚠️ DuckDuckGo поиск НЕ доступен - установите: pip install ddgs")

logger = logging.getLogger(__name__)

async def search_web(query):
    """Поиск в интернете через DuckDuckGo с фильтрацией"""
    if is_forbidden_query(query):
        print(f"⛔ Заблокирован запрос: {query}")
        return "FORBIDDEN"
    
    if not DDGS_AVAILABLE:
        logger.error("DuckDuckGo поиск недоступен")
        return None
    
    try:
        print(f"🔍 Поиск: {query}")
        
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3, safesearch='strict'))
            
            if not results:
                print(f"⚠️ Результатов не найдено для: {query}")
                return None
            
            print(f"✅ Найдено {len(results)} результатов")
            
            output = []
            for i, r in enumerate(results, 1):
                title = r.get('title', 'Без заголовка')
                body = r.get('body', 'Нет описания')
                href = r.get('href', '#')
                
                clean_href = clean_duckduckgo_url(href)
                title = title.replace('\n', ' ').strip()
                body = body.replace('\n', ' ').strip()
                
                output.append(f"{i}. {title}\n   {body}\n   🔗 {clean_href}")
            
            if not output:
                return None
            
            return "\n\n".join(output)
            
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        logger.error(f"Ошибка поиска: {e}")
        return None

async def search_web_fallback(query):
    """Запасной вариант поиска"""
    if is_forbidden_query(query):
        return "FORBIDDEN"
    
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"q": query, "kp": "-2"}, headers=headers, timeout=15) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    results = []
                    pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>'
                    links = re.findall(pattern, html)
                    
                    for i, (href, title) in enumerate(links[:3], 1):
                        clean_href = clean_duckduckgo_url(href)
                        clean_title = title.replace('\n', ' ').strip()
                        
                        results.append(f"{i}. {clean_title}\n   🔗 {clean_href}")
                    
                    if results:
                        return "\n\n".join(results)
        
        return None
    except Exception as e:
        print(f"❌ Ошибка fallback поиска: {e}")
        return None

def is_search_available():
    return DDGS_AVAILABLE