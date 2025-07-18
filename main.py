import httpx
import json
import logging
import asyncio
import random
import os
import base64
from datetime import datetime
import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

@register(
    "cngal_morning_report",
    "CnGal每日晨报",
    "发送“/晨报”或“/早报”即可获取当日最新信息喵~",
    "1.6.0",
    "https://github.com/yaoyuesuzu/cngal_morning_report"
)
class CngalMorningReportPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.base_url = "https://api.cngal.org"
        self.entry_page_url = "https://www.cngal.org/entries/index/"
        self.logger = logging.getLogger("CngalMorningReportPlugin")
        self.cst_tz = pytz.timezone('Asia/Shanghai')
        self.context = context

        # HTTP客户端设置
        headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36" }
        self.http_client = httpx.AsyncClient(timeout=30.0, verify=False, follow_redirects=True, headers=headers)
        
        self.logger.info("CnGal每日晨报插件已成功加载 (关键词触发模式)喵~")

    @filter.command("晨报", alias={"早报"})
    async def handle_morning_report_keyword(self, event: AstrMessageEvent):
        """处理晨报关键词，生成并发送报告"""
        self.logger.info(f"在频道 {event.session_id} 检测到晨报指令，开始生成报告...")
        yield event.plain_result("收到喵！正在为主人准备今天的晨报，请稍等哦~")
        
        report_chains = [chain async for chain in self.generate_morning_report()]
        
        for chain in report_chains:
            await event.send(MessageChain(chain))
            await asyncio.sleep(0.5) # 减慢发送速度，防止刷屏

    async def generate_morning_report(self):
        """产出晨报内容"""
        now = datetime.now(self.cst_tz)
        yield [Comp.Plain(f"主人早上好呀！今天是 {now.strftime('%Y年%m月%d日')}，可爱的我为您带来了今天的CnGal晨报☀️喵~")]

        # 今日寿星
        birthdays = await self._get_todays_birthdays(now.month, now.day)
        if birthdays:
            yield [Comp.Plain("\n🎂 今天是这些小可爱的生日哦~")]
            for role_info in birthdays:
                details = await self._get_details_by_id(role_info.get("id"))
                if details:
                    yield await self._format_role_reply(details)
        else:
            yield [Comp.Plain("\n🎂 今天似乎没有小可爱过生日呢~")]
        
        # 今日发售
        releases = await self._get_todays_releases(now.year, now.month, now.day)
        if releases:
            yield [Comp.Plain("\n🎮 今天有新游戏发售哦！")]
            for game_info in releases:
                details = await self._get_details_by_id(game_info.get("id"))
                if details:
                    yield await self._format_game_reply(details)
        else:
            # 如果没有发售游戏，则推荐一款随机游戏
            yield [Comp.Plain("\n🎮 今天没有新游戏发售呢，不过人家为主人推荐了一款游戏哦~")]
            try:
                game_names = await self._get_all_game_names()
                if game_names:
                    details = await self._get_details_by_name(random.choice(game_names))
                    if details:
                        yield await self._format_game_reply(details, is_recommend=True)
            except Exception as e:
                self.logger.error(f"生成无发售日备选推荐失败: {e}")

    def _parse_iso_datetime(self, date_string: str) -> datetime | None:
        if not date_string: return None
        try:
            parts = date_string.replace('Z', '').split('.')
            if len(parts) == 2:
                fractional_seconds = parts[1]
                if len(fractional_seconds) > 6: fractional_seconds = fractional_seconds[:6]
                date_string = f"{parts[0]}.{fractional_seconds}"
            naive_dt = datetime.fromisoformat(date_string)
            aware_utc_dt = naive_dt.replace(tzinfo=pytz.utc)
            return aware_utc_dt
        except (ValueError, TypeError) as e:
            self.logger.error(f"解析时间字符串 '{date_string}' 失败: {e}")
            return None

    async def _get_todays_birthdays(self, month: int, day: int) -> list:
        try:
            params = {"month": month, "day": day}
            response = await self.http_client.get(f"{self.base_url}/api/entries/GetRoleBirthdaysByTime", params=params)
            return response.json() if response.status_code == 200 else []
        except Exception as e: self.logger.error(f"API _get_todays_birthdays 失败: {e}"); return []

    async def _get_todays_releases(self, year: int, month: int, day: int) -> list:
        try:
            params = {"year": year, "month": month}
            response = await self.http_client.get(f"{self.base_url}/api/entries/GetPublishGamesByTime", params=params)
            if response.status_code != 200: return []
            
            today_games = []
            now_date = datetime.now(self.cst_tz).date()
            for game in response.json():
                publish_time_utc = self._parse_iso_datetime(game.get("publishTime"))
                if publish_time_utc and publish_time_utc.astimezone(self.cst_tz).date() == now_date:
                    today_games.append(game)
            return today_games
        except Exception as e: self.logger.error(f"API _get_todays_releases 失败: {e}"); return []
    
    async def _get_all_game_names(self) -> list:
        url = f"{self.base_url}/api/entries/GetAllEntries/Game"
        try:
            response = await self.http_client.get(url, timeout=20)
            return response.json() if response.status_code == 200 else []
        except Exception: self.logger.error(f"获取'Game'列表时发生错误"); return []

    async def _format_game_reply(self, details: dict, is_recommend: bool = False) -> list:
        message_chain = []
        image_bytes = await self._get_image_bytes(details.get('mainPicture'))
        if image_bytes: message_chain.append(Comp.Image.fromBytes(image_bytes))
        
        game_title = details.get('name', 'N/A')
        cngal_link = f"{self.entry_page_url}{details.get('id')}"
        
        text_lines = []
        if is_recommend:
            text_lines.append(f"【人家猜主人会喜欢这个喵~】\n《{game_title}》")
        else:
             text_lines.append(f"《{game_title}》")
        
        text_lines.append(f"简介: {details.get('briefIntroduction', '暂无')[:70]}...")
        text_lines.append(f"详情页: {cngal_link}")
        text_lines.append(f"详细情报请通过指令“ /cngal {game_title} ” 查询喵~")
        
        message_chain.append(Comp.Plain("\n".join(text_lines)))
        return message_chain

    async def _format_role_reply(self, details: dict) -> list:
        message_chain = []
        image_url = details.get('standingPainting') or details.get('mainPicture')
        image_bytes = await self._get_image_bytes(image_url)
        if image_bytes: message_chain.append(Comp.Image.fromBytes(image_bytes))
        
        role_name = details.get('name', 'N/A')
        cngal_link = f"{self.entry_page_url}{details.get('id')}"
        game_name = "未知"
        if details.get("addInfors") and details["addInfors"][0].get("contents"):
            game_name = details["addInfors"][0]["contents"][0].get("displayName", "未知")

        text_lines = [f"🎂 {role_name} 🎂"]
        # text_lines.append(f"来自:《{game_name}》") # 好像目前用不了，但我也不打算修了
        text_lines.append(f"详情页: {cngal_link}")
        text_lines.append(f"详细情报请通过指令“ /cngal {role_name} ” 查询喵~")
        
        message_chain.append(Comp.Plain("\n".join(text_lines)))
        return message_chain
        
    async def _get_image_bytes(self, url: str) -> bytes | None:
        if not url: return None
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            return await response.aread()
        except Exception: return None

    async def _get_details_by_name(self, name: str):
        item_id = await self._get_id_by_name(name)
        if item_id is None: return None
        return await self._get_details_by_id(item_id)
        
    def _custom_base64_encode_name(self, name: str) -> str:
        return 'A' + base64.urlsafe_b64encode(name.encode('utf-8')).decode('utf-8')

    async def _get_id_by_name(self, name: str):
        url = f"{self.base_url}/api/entries/GetId/{self._custom_base64_encode_name(name)}"
        try:
            response = await self.http_client.get(url)
            return response.json() if response.status_code == 200 else None
        except Exception: return None
        
    async def _get_details_by_id(self, item_id: int):
        url = f"{self.base_url}/api/entries/GetEntryView/{item_id}"
        try:
            response = await self.http_client.get(url)
            return response.json() if response.status_code == 200 else None
        except Exception: return None

    async def terminate(self):
        await self.http_client.aclose()
        self.logger.info("CnGal每日晨报插件已卸载。")
