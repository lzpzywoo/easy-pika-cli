"""Telegram bot: receive magnets, run relay pipeline."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from pikpakapi import PikPakApi

from .ai_parse import resolve_links
from .aria2 import Aria2Client
from .config import AppConfig
from .relay import RelayOptions, relay_magnet
from .session import load_session_async
from .token_helpers import TokenManager

logger = logging.getLogger(__name__)


class TelegramRelayBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._client: Optional[PikPakApi] = None
        self._token_mgr: Optional[TokenManager] = None

    async def _ensure_client(self) -> tuple[PikPakApi, TokenManager]:
        if self._client is None:
            self._client = await load_session_async(self.config.session_path)
        if self._token_mgr is None:
            self._token_mgr = TokenManager(self._client, self.config.session_path)
        await self._token_mgr.refresh()
        return self._client, self._token_mgr

    def _allowed(self, user_id: int) -> bool:
        allowed = self.config.telegram_allowed_users
        if not allowed:
            return True
        return user_id in allowed

    async def _handle_links(self, chat_id: int, text: str, reply) -> None:
        links = await resolve_links(
            text,
            use_llm=bool(self.config.openai_api_key),
            api_key=self.config.openai_api_key,
            base_url=self.config.openai_base_url,
            model=self.config.openai_model,
        )
        if not links:
            await reply("未识别到磁链或 .torrent 链接。")
            return

        client, token_mgr = await self._ensure_client()
        aria2 = None
        if self.config.download_backend == "aria2":
            aria2 = Aria2Client(
                self.config.aria2_rpc_url,
                self.config.aria2_rpc_secret,
            )

        for link in links:
            await reply(f"开始中转: {link[:80]}...")
            try:
                opts = RelayOptions(
                    upload=True,
                    wait=True,
                    download=True,
                    cleanup=self.config.relay_cleanup_cloud,
                    dest_dir=self.config.download_dir,
                    backend=self.config.download_backend,
                    aria2=aria2,
                    timeout=self.config.relay_timeout,
                    poll_interval=self.config.relay_poll_interval,
                )

                def on_log(msg: str) -> None:
                    logger.info("[%s] %s", chat_id, msg)

                result = await relay_magnet(
                    client, link, token_mgr, opts, on_log=on_log,
                )
                paths = "\n".join(str(p) for p in result.local_paths) or "(无本地文件)"
                await reply(
                    f"完成\n"
                    f"task: {result.task_id}\n"
                    f"files: {len(result.file_ids)}\n"
                    f"cleaned: {result.cleaned}\n"
                    f"{paths}"
                )
            except Exception as exc:
                logger.exception("relay failed")
                await reply(f"失败: {exc}")

    async def run_polling(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
        except ImportError as exc:
            raise ImportError(
                "Telegram bot requires python-telegram-bot. "
                "Install: pip install python-telegram-bot"
            ) from exc

        token = self.config.telegram_token
        if not token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN environment variable")

        bot = self

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_user or not bot._allowed(update.effective_user.id):
                await update.message.reply_text("未授权。")
                return
            await update.message.reply_text(
                "发送磁链或 .torrent 链接，将自动：\n"
                "1. 提交 PikPak 离线下载\n"
                "2. 等待云端完成\n"
                "3. 下载到本地\n"
                "4. 清理网盘文件\n\n"
                "也可分步：/upload /wait /download /cleanup（开发中请用完整消息）"
            )

        async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.effective_user:
                return
            if not bot._allowed(update.effective_user.id):
                await update.message.reply_text("未授权。")
                return
            text = update.message.text or ""
            if text.startswith("/"):
                return

            async def reply(msg: str) -> None:
                await update.message.reply_text(msg)

            await bot._handle_links(update.effective_chat.id, text, reply)

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

        logger.info("Telegram bot polling...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def run_telegram_bot(config: Optional[AppConfig] = None) -> None:
    cfg = config or AppConfig.from_env()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(TelegramRelayBot(cfg).run_polling())
