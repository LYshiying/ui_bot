import asyncio
import os
import json
import re

from nonebot import CommandSession
from aiocqhttp import MessageSegment
from soraha_utils.uiclient import async_uiclient

import config as cfg
from src.Services import uiPlugin, SUPERUSER
from soraha_utils import retry, logger, async_uiclient


bot_name = cfg.bot_name

sv_help = f"""搞的很快的插件！ | 使用帮助
括号内的文字即为指令,小括号内为可选文字(是否必带请自行参照使用示例)
[色图(1~100)份 (关键字) (r18)] -> 获取色图，默认一份，输入小括号内可选的文字获取色图
    参数详解:
        数量 -> 限定为1-100,太多非常容易风控且获取速度很慢,请控制在最大十几张以内,乱来的话bot不会响应
        关键字 -> 仅限一个词语,并且与pixiv标签对应(中文日文均可)
        r18 -> 是否获取r18色图,此选项并非混杂,而是纯r18 or 一个不带
    特别注意:
        风控问题 -> 由于QQ非常奇怪,经常会发不出来,如果长时间没有发送请当做风控没发出来,因此请尽量获取5张或以下的色图
        内鬼问题 -> 由于各个群有内鬼,加上请求的api不能保证绝对没有r18,因此对某些群不开放,如果需要私聊使用请通知管理员！
        数量问题 -> 获取的图片数量不一定准确 有两个原因
                       -> 该关键字下仅有那么多图,如果你要10张冷门tag,而api只有3张则只会传回三张
                       -> 图片获取出现错误,获取图片出错时会自动忽略该张图片
    使用示例:
        色图 -> 获取一份色图
        色图十份 碧蓝航线 -> 获取十份碧蓝航线的色图！
        色图30份 {bot_name} r18 -> 获得3份{bot_name}酱的r18色图！(当然这是禁止的！禁止获取{bot_name}酱的任何色图！)(其实可能也没有啦！)
[(开启|关闭)原图] -> 顾名思义：开启后会发送原图,各个群/私聊独立设置
    特别注意:
        原图问题 -> 由于有些图片可能超过10MB 20MB 下载速度会非常慢 可能很久才发的出来
        默认情况 -> 原图默认关闭(即获取非原图)
    使用示例:
        开启原图 -> setu功能开启原图
        关闭原图 -> setu功能关闭原图
""".strip()
sv = uiPlugin(
    ["setu", "获取色图"],
    True,
    usage=sv_help,
    use_cache_folder=True,
    perm_manager=SUPERUSER,
    private_use=True,
)

if os.path.exists(os.path.join(os.getcwd(), "src", "plugins", "setu", "config.json")):
    with open(
        os.path.join(os.getcwd(), "src", "plugins", "setu", "config.json"),
        "r",
        encoding="utf-8",
    ) as f:
        self_config = json.load(f)
else:
    with open(
        os.path.join(os.getcwd(), "src", "plugins", "setu", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
        self_config = {"group": {}, "private": {}}
        json.dump(self_config, f, indent=4, ensure_ascii=False)


@sv.ui_command("开启原图", patterns=r"^(开启|关闭)原图")
async def set_original(session):
    """开关原图

    self_config中分别装着group跟private
    其中键为群号或qq号
    True为开启原图

    Args:
        session: bot封装的信息
    """
    if session.event.detail_type == "group":
        gid = str(session.event["group_id"])
    else:
        gid = None
    uid = str(session.event["user_id"])
    if "开启" in session.event["raw_message"]:
        if gid:
            self_config["group"][gid] = True
        else:
            self_config["private"][uid] = True
    else:
        if gid:
            self_config["group"][gid] = False
        else:
            self_config["private"][uid] = False
    with open(
        os.path.join(os.getcwd(), "src", "plugins", "setu", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(self_config, f, ensure_ascii=False, indent=4)
    await session.send(session.event["raw_message"][:2] + "成功")


@sv.ui_command("色图", patterns=r"^[色|涩]图([1-9][0-9]{0,1}|100)([份|张])", privileged=True)
async def get_setu(session):
    """获取色图

    Args:
        session: bot封装的信息
    """
    num = session.get("num")
    keyword = session.get("keyword")
    r18 = session.get("r18")
    await session.send("正在获取setu(太久没发出来就是风控了)")

    try:
        result = await get_api(session, keyword, r18, num)
    except Exception as e:
        await session.finish("涩图获取失败惹……")
        logger.error(f"色图获取失败: {e}")

    if "count" not in result:
        return

    coro = []
    for i in range(result["count"]):
        gid = (
            session.event["user_id"]
            if session.event.detail_type == "private"
            else session.event["group_id"]
        )
        coro.append(
            process(result["data"][i], session.event.detail_type == "private", gid)
        )
    img_data = await asyncio.gather(*coro, return_exceptions=True)
    img_data = [x for x in img_data if isinstance(x, str)]
    msg = "\n".join(img_data)
    await session.send(msg.strip(), at_sender=True)


async def process(result: dict, to_me: bool, gid: int) -> str:
    """处理单个图片的json

    Args:
        result: 单个图片的json字典
        to_me: 是否私聊
        gid: 群号 用于传给get_image判断是否需要原图 -1的意思是私聊 默认原图

    Return:
        可以直接发送的字符串以及CQ code
    """
    seq = await get_image(result["url"], to_me, gid)
    msg = (
        f"作者:{result['author']}(id:{result['uid']})\n"
        f"标题:{result['title']}(id:{result['pid']})\n"
        f"图片链接:{result['url']}\n"
        f"tags:" + "，".join(result["tags"]) + "\n"
        f"{seq}"
    )
    return msg


@retry()
async def get_image(url: str, to_me: bool, gid: int) -> str:
    """处理涩图

    尝试重新捏造原图与非原图的色图链接,判断是否要获取原图
    然后请求涩图并且保存在res/setu文件夹下

    Args:
        url: 色图的原图连接
        to_me: 是否私聊
        gid: 群号

    Return:
        图片的CQ code
    """
    gid = str(gid)
    original = False
    if to_me:
        if gid in self_config["private"]:
            if self_config["private"][gid]:
                original = True
    else:
        if gid in self_config["group"]:
            if self_config["group"][gid]:
                original = True
    if not original:
        url = url.replace("img-original", "c/480x960/img-master")
        if ".jpg" in url:
            url = url.replace(".jpg", "_master1200.jpg")
        url = url.replace(".png", "_master1200.jpg")
    if not cfg.proxy_pixiv:
        url = url.replace("i.pixiv.cat", "i.pximg.net")

    header = {"referer": "https://www.pixiv.net/"}
    async with async_uiclient(
        proxy=cfg.proxies_for_all, other_headers=header
    ) as client:
        res = await client.uiget(url)
    content = res.content
    if "_master1200.jpg" in url:
        url = url.replace("_master1200.jpg", "")
    folder_name = url.rsplit("/", 1)[1]
    path = os.path.join(
        cfg.res,
        "cacha",
        "setu",
        folder_name.replace(".jpg", "").replace(".png", "") + ".png",
    )
    with open(path, "wb") as f:
        f.write(content)
    path = r"file:///" + path
    seq = MessageSegment.image(path)
    return seq


@retry()
async def get_api(session: CommandSession, keyword: str, r18: int, num: int) -> dict:
    """请求api页面

    构造请求信息, 拿回涩图

    Args:
        session: 机器人封装的信息
        keyword: 关键字
        r18: str表示开关是否拿回r18图片
        num: 需要图片的数量

    Return:
        返回图片的json字典
    """
    urls = "https://api.lolicon.app/setu/v1"
    data = {"keyword": keyword, "r18": r18, "num": int(num)}
    async with async_uiclient(request_params=data, proxy=cfg.proxies_for_all) as client:
        res = await client.uiget(urls)
    js = res.json()
    return (
        js
        if js["msg"] != "没有符合条件的色图"
        else await session.send("没有符合条件的涩图", at_sender=True)
    )


@get_setu.args_parser
async def _(session: CommandSession):
    """解析传入的指令与参数"""
    com = session.ctx.raw_message.strip().split(" ")

    num = re.findall(r"[色|涩]图([1-9][0-9]{0,1}|100)(?:[份|张])", com[0])
    if not num:
        num = [1]
    session.state["num"] = num[0]
    com.pop(0)

    session.state["r18"] = 0
    for i in com:
        if i == "R18" or i == "r18":
            session.state["r18"] = 1
            try:
                com.remove("r18")
            except ValueError:
                com.remove("R18")
            break

    if not com:
        session.state["keyword"] = ""
    elif len(com) == 1:
        session.state["keyword"] = com[0]
    else:
        session.state["keyword"] = ""
