"""VLM 状态与图片理解 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from starlette import status

from src.config import config
from src.dependencies import get_current_user, get_vision_client
from src.exceptions import BadRequestException
from src.services.vision import VisionClient, image_mime

router = APIRouter(prefix="/vlm", tags=["vlm"])


@router.get("/status")
async def vlm_status(
    vision: VisionClient = Depends(get_vision_client),
    _user=Depends(get_current_user),
):
    return {"code": status.HTTP_200_OK, "data": vision.public_status()}


@router.post("/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    prompt: str = Form("", max_length=4000),
    caption: str = Form("", max_length=1000),
    page: int | None = Form(None, ge=1),
    vision: VisionClient = Depends(get_vision_client),
    _user=Depends(get_current_user),
):
    """上传单张图表/公式图片，经当前 OpenAI 兼容 VLM 返回事实摘要。"""
    mime = image_mime(file.filename or "", file.content_type or "")
    if not mime:
        raise BadRequestException("仅支持 PNG/JPEG/WEBP/GIF/BMP 图片")

    max_bytes = config.max_upload_mb * 1024 * 1024
    payload = await file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise BadRequestException(f"图片超过大小限制（{config.max_upload_mb}MB）")

    analysis = vision.analyze_bytes(
        payload,
        mime=mime,
        prompt=prompt,
        caption=caption,
        page=page,
    )
    return {
        "code": status.HTTP_200_OK,
        "data": {
            "analysis": analysis,
            "model": vision.model,
            "filename": file.filename or "image",
            "mime_type": mime,
            "page": page,
        },
    }
