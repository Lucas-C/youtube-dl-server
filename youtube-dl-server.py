import os
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from starlette.status import HTTP_303_SEE_OTHER
from starlette.applications import Starlette
from starlette.config import Config
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.background import BackgroundTask

from yt_dlp import YoutubeDL

templates = Jinja2Templates(directory="templates")
config = Config(".env")

app_defaults = {
    "YDL_FORMAT": config("YDL_FORMAT", cast=str, default="bestvideo+bestaudio/best"),
    "YDL_EXTRACT_AUDIO_FORMAT": config("YDL_EXTRACT_AUDIO_FORMAT", default=None),
    "YDL_EXTRACT_AUDIO_QUALITY": config(
        "YDL_EXTRACT_AUDIO_QUALITY", cast=str, default="192"
    ),
    "YDL_RECODE_VIDEO_FORMAT": config("YDL_RECODE_VIDEO_FORMAT", default=None),
    "YDL_OUTPUT_TEMPLATE": config(
        "YDL_OUTPUT_TEMPLATE",
        cast=str,
        default=config("YDL_DEST_DIR", cast=str, default="/youtube-dl") + "/" + config("YDL_FILE_FORMAT", default="%(title).200s [%(id)s].%(ext)s"),
    ),
    "YDL_ARCHIVE_FILE": config("YDL_ARCHIVE_FILE", default=None),
    "YDL_UPDATE_TIME": config("YDL_UPDATE_TIME", cast=bool, default=True),
    "YDL_CACHE_DIR": config("YDL_CACHE_DIR", cast=bool, default=False),
}
template_env = {
    "BASE_URL": config("YDL_BASE_URL", cast=str, default="/youtube-dl"),
    "COVER_IMG": config("YDL_COVER_IMG", cast=str, default=""),
    "ROBOTS_NOINDEX": config("YDL_ROBOTS_NOINDEX", cast=bool, default=False)
}


async def index(request):
    return templates.TemplateResponse(
        "index.html", {**template_env, "request": request}
    )


async def redirect_to_index(request):
    return RedirectResponse(url="/youtube-dl")


async def q_put(request):
    form = await request.form()
    url = form.get("url").strip()
    ui = form.get("ui")
    options = {"format": form.get("format")}

    if not url:
        return JSONResponse(
            {"success": False, "error": "/q called without a 'url' in form data"}
        )

    parsed_url = urlparse(url)
    qparams = dict(parse_qsl(parsed_url.query))
    if 'list' in qparams:
        # This means that the video is part of a playlist:
        # we need to remove the 'list' query param,
        # otherwise yt-dlp will download the entire playlist.
        del qparams['list']
        url = urlunparse(parsed_url._replace(query=urlencode(qparams)))

    processing.add(url)
    task = BackgroundTask(download, url, options)

    print(f"Added url {url} to the download queue")

    if not ui:
        return JSONResponse(
            {"success": True, "url": url, "options": options}, background=task
        )
    return templates.TemplateResponse(
        "processing.html", {
            **template_env,
            "request": request,
            "url": quote(url, safe=''),
            "generated_file": f"{qparams['v']}.mp3"
        },
        background=task
    )


async def q_get(request):
    return JSONResponse({"processing": list(processing)})


async def q_is_complete(request):
    url = request.query_params["url"]
    return JSONResponse({"complete": url not in processing })


def get_ydl_options(request_options):
    request_vars = {}

    requested_format = request_options.get("format", "bestvideo")
    if requested_format in ["aac", "flac", "mp3", "m4a", "opus", "vorbis", "wav"]:
        request_vars["YDL_EXTRACT_AUDIO_FORMAT"] = requested_format
    elif requested_format == "bestaudio":
        request_vars["YDL_EXTRACT_AUDIO_FORMAT"] = "best"
    elif requested_format in ["mp4", "flv", "webm", "ogg", "mkv", "avi"]:
        request_vars["YDL_RECODE_VIDEO_FORMAT"] = requested_format

    ydl_vars = app_defaults | request_vars

    postprocessors = []

    if ydl_vars["YDL_EXTRACT_AUDIO_FORMAT"]:
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ydl_vars["YDL_EXTRACT_AUDIO_FORMAT"],
                "preferredquality": ydl_vars["YDL_EXTRACT_AUDIO_QUALITY"],
            }
        )

    if ydl_vars["YDL_RECODE_VIDEO_FORMAT"]:
        postprocessors.append(
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": ydl_vars["YDL_RECODE_VIDEO_FORMAT"],
            }
        )

    return {
        "format": ydl_vars["YDL_FORMAT"],
        "postprocessors": postprocessors,
        "outtmpl": ydl_vars["YDL_OUTPUT_TEMPLATE"],
        "download_archive": ydl_vars["YDL_ARCHIVE_FILE"],
        "updatetime": ydl_vars["YDL_UPDATE_TIME"],
        "cachedir": ydl_vars["YDL_CACHE_DIR"],
    }


def download(url, request_options):
    with YoutubeDL(get_ydl_options(request_options)) as ydl:
        ydl.download([url])
    processing.remove(url)


processing = set()

routes = [
    Route("/", endpoint=redirect_to_index),
    Route("/youtube-dl", endpoint=index),
    Route("/youtube-dl/q", endpoint=q_put, methods=["POST"]),
    Route("/youtube-dl/q", endpoint=q_get),
    Route("/youtube-dl/q_is_complete", endpoint=q_is_complete),
    Mount("/youtube-dl/static", app=StaticFiles(directory="static"), name="static"),
]

app = Starlette(debug=True, routes=routes)

