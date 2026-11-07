"""CapCut Mac 드래프트 생성기.

실측 근거는 스킬 pycapcut-mac 참조 (~/.claude/skills/pycapcut-mac/). 요약:
  - draft_info.json (draft_content.json 아님)
  - 시간 단위 마이크로초
  - tm_duration == draft_info.duration 필수
  - 세그먼트마다 extra_material_refs 5종 고유 인스턴스
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .probe import US, VideoInfo

DRAFT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"

VERSION = 360000
# CapCut 9.0.0 이 드래프트를 열고 다시 저장했을 때 쓴 값 (구버전 드래프트는 133.0.0)
NEW_VERSION = "177.0.0"


def frame_align(us: int, fps: float) -> int:
    """마이크로초를 프레임 경계로 스냅.

    CapCut 은 드래프트를 열면서 모든 시간값을 프레임 배수로 정규화한다.
    미리 맞춰두면 앱이 값을 바꾸지 않아 우리가 계산한 것과 실제가 일치한다.
    """
    frame_us = 1_000_000 / fps
    return int(round(us / frame_us) * frame_us)


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _lid() -> str:
    return str(uuid.uuid4()).lower()


def _extra_materials() -> tuple[dict[str, list], list[str]]:
    """세그먼트 1개용 부속 머티리얼 5종 생성 → (버킷별 항목, ref ID 목록)."""
    speed = {"curve_speed": None, "id": _uid(), "mode": 0, "speed": 1.0,
             "type": "speed"}
    placeholder = {"error_path": "", "error_text": "", "id": _uid(),
                   "meta_type": "none", "res_path": "", "res_text": "",
                   "type": "placeholder_info"}
    canvas = {"album_image": "", "blur": 0.0, "color": "", "id": _uid(),
              "image": "", "image_id": "", "image_name": "",
              "source_platform": 0, "team_id": "", "type": "canvas_color"}
    scm = {"audio_channel_mapping": 0, "id": _uid(), "is_config_open": False,
           "type": ""}
    vocal = {"choice": 0, "id": _uid(), "production_path": "",
             "removed_sounds": [], "time_range": None,
             "type": "vocal_separation"}
    # CapCut 이 스스로 저장할 때 세그먼트마다 붙이는 6번째 참조
    color = {"id": _uid(), "is_color_clip": False, "is_gradient": False,
             "solid_color": "", "gradient_colors": [], "gradient_percents": [],
             "gradient_angle": 90.0, "width": 0.0, "height": 0.0}

    buckets = {
        "speeds": [speed],
        "placeholder_infos": [placeholder],
        "canvases": [canvas],
        "sound_channel_mappings": [scm],
        "vocal_separations": [vocal],
        "material_colors": [color],
    }
    refs = [speed["id"], placeholder["id"], canvas["id"], scm["id"],
            vocal["id"], color["id"]]
    return buckets, refs


def _video_material(video: VideoInfo, local_id: str) -> dict:
    return {
        "aigc_type": "none", "audio_fade": None, "cartoon_path": "",
        "category_id": "", "category_name": "local", "check_flag": 62978047,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0,
                 "lower_right_x": 1.0, "lower_right_y": 1.0,
                 "upper_left_x": 0.0, "upper_left_y": 0.0,
                 "upper_right_x": 1.0, "upper_right_y": 0.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "duration": video.duration_us,
        "extra_type_option": 0, "formula_id": "", "freeze": None,
        "has_audio": video.has_audio, "has_sound_separated": False,
        "height": video.height, "id": _uid(),
        "intensifies_audio_path": "", "intensifies_path": "",
        "is_ai_generate_content": False, "is_copyright": False,
        "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
        "live_photo_cover_path": "", "live_photo_timestamp": -1,
        "local_id": "", "local_material_from": "",
        "local_material_id": local_id,
        "material_id": "", "material_name": video.name, "material_url": "",
        "matting": {"custom_matting_id": "", "enable_matting_stroke": False,
                    "expansion": 0, "feather": 0, "flag": 0,
                    "has_use_quick_brush": False, "has_use_quick_eraser": False,
                    "interactiveTime": [], "path": "", "reverse": False,
                    "strokes": []},
        "media_path": "", "object_locked": None, "origin_material_id": "",
        "path": str(video.path), "picture_from": "none",
        "request_id": "", "reverse_intensifies_path": "", "reverse_path": "",
        "smart_match_info": None, "smart_motion": None,
        "source": 0, "source_platform": 0,
        "stable": {"matrix_path": "", "stable_level": 0,
                   "time_range": {"duration": 0, "start": 0}},
        "team_id": "", "type": "video",
        "video_algorithm": {"ai_background_configs": [], "algorithms": [],
                            "gameplay_configs": [], "path": "",
                            "time_range": None},
        "width": video.width,
    }


# ── 자막 ─────────────────────────────────────────────────────────────
# 값 근거: 사용자가 CapCut 에서 직접 만든 자막을 실측
# (스킬 pycapcut-mac: references/draft-format.md §6-7)
FONT_DIR = Path("/Applications/CapCut.app/Contents/Resources/Font/SystemFont")
FONT_KO = FONT_DIR / "ko.ttf"          # 한글용. CapCut 기본값은 en.ttf 라 대체
SUBTITLE_Y = -0.77                     # 화면 하단. 0=중앙, -1=바닥
TEXT_RENDER_INDEX = 14000
TEXT_SIZE = 21                         # CapCut 기본은 30 — 한국어 자막엔 큼

# 자막 배경 기본값. 밝은 화면에서도 흰 글자가 읽히도록 반투명 검정 바.
BG_COLOR = "#000000"
BG_ALPHA = 0.70

# ★ check_flag 는 비트 플래그. 배경을 켜려면 색·스타일만으로는 부족하고
#   여기 비트 16 을 세워야 렌더러가 배경을 그린다.
#   실측: 사용자가 CapCut UI 에서 배경을 켠 자막 = 23, 안 켠 자막 = 7.
CHECK_FLAG_BASE = 7
CHECK_FLAG_BG = 16


def _text_material(text: str, font: Path, size: int, color: str,
                   bg_color: str | None = BG_COLOR,
                   bg_alpha: float = BG_ALPHA) -> dict:
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    # 크기는 text_size(30) 와 content.size/font_size(10) 두 곳에 나뉘어 있고
    # 어느 쪽이 실제 렌더를 좌우하는지 미확인이다. 같은 비율로 함께 줄인다.
    base = 10.0 * size / 30.0
    content = json.dumps({
        "styles": [{
            "fill": {"content": {"solid": {"color": rgb},
                                 "render_type": "solid"}},
            "range": [0, len(text)],
            "size": base,
            "font": {"path": str(font), "id": ""},
        }],
        "text": text,
    }, ensure_ascii=False)

    # 배경 바. CapCut 이 꺼둔 상태로 쓴 값은 color="" / style=0 이므로
    # 켜는 쪽은 색을 채우고 style 을 1 로 둔다. (실측 미확인 — 검증 필요)
    bg_on = bool(bg_color)
    return {
        "alignment": 1,
        "background_alpha": bg_alpha if bg_on else 1.0,
        "background_color": bg_color or "",
        "background_fill": "",
        "background_style": 1 if bg_on else 0,
        "background_height": 0.14,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.12 if bg_on else 0.0,
        "background_vertical_offset": 0.0, "background_width": 0.14,
        "bold_width": 0.0, "border_alpha": 1.0,
        "border_color": "", "border_mode": 0, "border_width": 0.08,
        # 배경이 안 먹더라도 글자가 읽히도록 그림자는 켜둔다 (이중 안전장치)
        "has_shadow": True, "shadow_color": "#000000",
        "caption_template_info": {
            "resource_id": "", "third_resource_id": "", "resource_name": "",
            "category_id": "", "category_name": "", "effect_id": "",
            "request_id": "", "path": "", "is_new": False,
            "source_platform": 0},
        "check_flag": CHECK_FLAG_BASE | (CHECK_FLAG_BG if bg_on else 0),
        "combo_info": {"text_templates": []},
        "content": content,
        "current_words": {"start_time": [], "end_time": [], "text": []},
        "fixed_height": -1.0, "fixed_width": -1.0,
        "font_path": str(font), "font_size": base, "font_title": "none",
        "global_alpha": 1.0, "id": _uid(), "initial_scale": 1.0,
        "inner_padding": -1.0, "layer_weight": 1, "letter_spacing": 0.0,
        "line_feed": 1, "line_max_width": 0.82, "line_spacing": 0.02,
        "lyrics_template": {"resource_id": "", "resource_name": "",
                            "panel": "", "effect_id": "", "path": "",
                            "category_id": "", "category_name": "",
                            "request_id": ""},
        "multi_language_current": "none", "offset_on_path": 0.0,
        "shadow_alpha": 0.9, "shadow_angle": -45.0, "shadow_distance": 5.0,
        "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678927},
        "shadow_smoothing": 0.45,
        "shadow_thickness_projection_angle": 0.0,
        "shadow_thickness_projection_distance": 0.0,
        "shadow_thickness_projection_enable": False,
        "single_char_bg_alpha": 1.0, "single_char_bg_color": "",
        "single_char_bg_enable": False, "single_char_bg_height": 0.0,
        "single_char_bg_horizontal_offset": 0.0,
        "single_char_bg_round_radius": 0.3,
        "single_char_bg_vertical_offset": 0.0, "single_char_bg_width": 0.0,
        "style_name": "",
        "sub_template_id": -1, "subtitle_template_original_fontsize": 0.0,
        "text_alpha": 1.0, "text_color": color, "text_size": size,
        "type": "text", "underline_offset": 0.22, "underline_width": 0.05,
        "use_effect_default_color": True,
        "words": {"start_time": [], "end_time": [], "text": []},
    }


def _text_segment(material_id: str, anim_id: str, start: int, dur: int,
                  y: float) -> dict:
    return {
        "id": _uid(),
        "source_timerange": None,   # ★ 텍스트는 원본 시간축이 없다
        "target_timerange": {"start": start, "duration": dur},
        "render_timerange": {"start": 0, "duration": 0},
        "desc": "", "state": 0, "speed": 1.0, "is_loop": False,
        "is_tone_modify": False, "reverse": False,
        "intensifies_audio": False, "cartoon": False,
        "volume": 1.0, "last_nonzero_volume": 1.0,
        "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                 "transform": {"x": 0.0, "y": y},
                 "flip": {"vertical": False, "horizontal": False},
                 "alpha": 1.0},
        "uniform_scale": {"on": True, "value": 1.0},
        "material_id": material_id,
        "extra_material_refs": [anim_id],   # sticker_animation 1개뿐
        "render_index": TEXT_RENDER_INDEX, "keyframe_refs": [],
        "enable_lut": False, "enable_adjust": False, "enable_hsl": False,
        "visible": True, "group_id": "",
        "enable_color_curves": True, "enable_hsl_curves": True,
        "track_render_index": 1, "hdr_settings": None,
        "enable_color_wheels": True, "track_attribute": 0,
        "is_placeholder": False, "template_id": "",
        "enable_smart_color_adjust": False, "template_scene": "default",
        "common_keyframes": [], "caption_info": None,
        "responsive_layout": {"enable": False, "target_follow": "",
                              "size_layout": 0, "horizontal_pos_layout": 0,
                              "vertical_pos_layout": 0},
        "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False, "enable_adjust_mask": False,
        "raw_segment_id": "", "lyric_keyframes": None,
        "enable_video_mask": True, "digital_human_template_group_id": "",
        "color_correct_alg_result": "", "source": "segmentsourcenormal",
        "enable_mask_stroke": False, "enable_mask_shadow": False,
        "enable_color_adjust_pro": False, "segment_color_tag": "",
    }


def _segment(material_id: str, refs: list[str], src_start: int,
             dur: int, tgt_start: int) -> dict:
    return {
        "caption_info": None, "cartoon": False,
        "clip": {"alpha": 1.0,
                 "flip": {"horizontal": False, "vertical": False},
                 "rotation": 0.0,
                 "scale": {"x": 1.0, "y": 1.0},
                 "transform": {"x": 0.0, "y": 0.0}},
        "color_correct_alg_result": "", "common_keyframes": [], "desc": "",
        "digital_human_template_group_id": "",
        "enable_adjust": True, "enable_adjust_mask": False,
        "enable_color_correct_adjust": False, "enable_color_curves": True,
        "enable_color_match_adjust": False, "enable_color_wheels": True,
        "enable_hsl": False, "enable_lut": True,
        "enable_smart_color_adjust": False, "enable_video_mask": True,
        "extra_material_refs": refs,
        "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "id": _uid(), "intensifies_audio": False, "is_loop": False,
        "is_placeholder": False, "is_tone_modify": False,
        "keyframe_refs": [], "last_nonzero_volume": 1.0,
        "lyric_keyframes": None, "material_id": material_id,
        "raw_segment_id": "", "render_index": 0,
        "render_timerange": {"duration": 0, "start": 0},
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0,
                              "size_layout": 0, "target_follow": "",
                              "vertical_pos_layout": 0},
        "reverse": False,
        "source_timerange": {"duration": dur, "start": src_start},
        "speed": 1.0, "state": 0,
        "target_timerange": {"duration": dur, "start": tgt_start},
        "template_id": "", "template_scene": "default",
        "track_attribute": 0, "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True, "volume": 1.0,
    }


def build_draft(video: VideoInfo, keeps: list[tuple[int, int]],
                name: str, root: Path | None = None,
                copy_media: bool = True,
                cues: list | None = None,
                subtitle_y: float = SUBTITLE_Y,
                text_size: int = TEXT_SIZE,
                text_color: str = "#FFFFFF",
                bg_color: str | None = BG_COLOR,
                bg_alpha: float = BG_ALPHA) -> Path:
    """점프컷 드래프트를 만들고 폴더 경로를 반환.

    copy_media=True 이면 원본을 드래프트 폴더 안으로 복사한다.
    ★ CapCut.app 은 샌드박스라 사용자가 파일 선택창으로 고른 경로 외에는
      읽지 못한다. 드래프트 폴더는 CapCut 자신이 읽고 쓰는 곳이므로
      미디어를 그 안에 두어야 "파일에 액세스할 수 없음" 을 피한다.
    """
    root = root or DRAFT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    folder = root / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    if copy_media:
        media_dir = folder / "Resources" / "local"
        media_dir.mkdir(parents=True, exist_ok=True)
        dest = media_dir / video.path.name
        shutil.copy2(video.path, dest)
        video = replace(video, path=dest)

    local_id = _lid()
    vmat = _video_material(video, local_id)

    materials: dict[str, list] = {
        "videos": [vmat], "speeds": [], "placeholder_infos": [],
        "canvases": [], "sound_channel_mappings": [], "vocal_separations": [],
        "material_colors": [],
        "audios": [], "texts": [], "material_animations": [], "effects": [],
        "transitions": [], "stickers": [], "video_effects": [],
        "audio_effects": [], "audio_fades": [], "beats": [], "filters": [],
        "images": [], "masks": [], "shapes": [],
    }

    # ★ 30fps 프레임 길이는 33333.333…us (정수 아님). 매 세그먼트마다 us 를
    #   프레임으로 반올림하고 다시 us 로 버리면 오차가 누적된다(실측: 29개 세그먼트에서
    #   26건 어긋남). 정수 프레임으로 계산하고, 경계 us 는 매번 round(프레임×길이) 로
    #   구해 누적 드리프트를 없앤다. duration 은 인접 경계의 차라 합이 정확히 맞는다.
    frame_us = US / video.fps

    def to_us(frames: int) -> int:
        return int(round(frames * frame_us))

    # 원본이 가진 총 프레임 수 — source 범위가 이걸 넘으면 CapCut 이 거부/오작동한다
    total_src_frames = int(video.duration_us // frame_us)

    segments = []
    tgt_frames = 0
    for src_start, src_end in keeps:
        src_f = round(src_start / frame_us)
        dur_f = round((src_end - src_start) / frame_us)
        # 마지막 프레임이 반올림으로 원본 끝을 넘지 않게 클램프 (실측: 0.014s 초과)
        if src_f + dur_f > total_src_frames:
            dur_f = total_src_frames - src_f
        if dur_f <= 0:
            continue
        buckets, refs = _extra_materials()
        for bucket, items in buckets.items():
            materials[bucket].extend(items)
        # 경계를 프레임 누적으로 잡아 us 로 변환 → 드리프트 없음
        src_start_us = to_us(src_f)
        tgt_start_us = to_us(tgt_frames)
        dur_us = to_us(tgt_frames + dur_f) - tgt_start_us
        segments.append(
            _segment(vmat["id"], refs, src_start_us, dur_us, tgt_start_us))
        tgt_frames += dur_f

    total = to_us(tgt_frames)  # ★ tm_duration 과 반드시 동일해야 함

    # --- 자막 트랙 ---
    tracks: list[dict] = [{
        "attribute": 0, "flag": 0, "id": _uid(),
        "is_default_name": True, "name": "",
        "segments": segments, "type": "video",
    }]
    font = FONT_KO if FONT_KO.exists() else FONT_DIR / "en.ttf"

    # 프레임 정렬은 start 와 end 를 각각 맞춘다.
    # start 와 duration 을 따로 반올림하면 끝이 다음 자막 시작을 넘어 겹친다.
    aligned: list[tuple[int, int, str]] = []
    for cue in sorted(cues or [], key=lambda c: c.start):
        s = frame_align(cue.start, video.fps)
        e = frame_align(cue.end, video.fps)
        if e > s:
            aligned.append((s, e, cue.text))
    # 정렬 후 남은 겹침을 잘라낸다
    for i in range(len(aligned) - 1):
        s, e, t = aligned[i]
        nxt = aligned[i + 1][0]
        if e > nxt:
            aligned[i] = (s, nxt, t)

    text_segments = []
    for start, end, text in aligned:
        dur = end - start
        if dur <= 0 or start >= total:
            continue
        dur = min(dur, total - start)   # 타임라인 밖으로 넘치지 않게
        tmat = _text_material(text, font, text_size, text_color,
                              bg_color, bg_alpha)
        anim = {"id": _uid(), "type": "sticker_animation", "animations": [],
                "multi_language_current": "none"}
        materials["texts"].append(tmat)
        materials["material_animations"].append(anim)
        text_segments.append(
            _text_segment(tmat["id"], anim["id"], start, dur, subtitle_y))

    if text_segments:
        tracks.append({
            "attribute": 0, "flag": 0, "id": _uid(),
            "is_default_name": True, "name": "",
            "segments": text_segments, "type": "text",
        })
    now_us = int(time.time() * US)
    now_s = int(time.time())

    draft_info = {
        "canvas_config": {"background": None, "height": video.height,
                          "ratio": "original", "width": video.width},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1, "attachment_info": [],
            "combination_max_index": 1, "export_range": None,
            "extract_audio_last_index": 1, "lyrics_recognition_id": "",
            "lyrics_sync": True, "lyrics_taskinfo": [],
            "maintrack_adsorb": True, "material_save_mode": 0,
            "multi_language_current": "none", "multi_language_list": [],
            "multi_language_main": "none", "multi_language_mode": "none",
            "original_sound_last_index": 1, "record_audio_last_index": 1,
            "sticker_max_index": 1, "subtitle_keywords_config": None,
            "subtitle_recognition_id": "", "subtitle_sync": True,
            "subtitle_taskinfo": [], "system_font_list": [],
            "use_float_render": False, "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None, "create_time": 0,
        "duration": total,
        "extra_info": None, "fps": video.fps,
        "free_render_index_mode_on": False, "group_container": None,
        "id": _uid(), "is_drop_frame_timecode": False,
        "keyframe_graph_list": [], "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "last_modified_platform": {
            "app_id": 359289, "app_source": "cc", "app_version": "9.0.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "",
            "os": "mac", "os_version": "",
        },
        "lyrics_effects": [],
        "materials": materials,
        "mutable_config": None, "name": "",
        "new_version": NEW_VERSION,
        "path": "", "platform": {
            "app_id": 359289, "app_source": "cc", "app_version": "9.0.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "",
            "os": "mac", "os_version": "",
        },
        "relationships": [], "render_index_track_mode_on": True,
        "retouch_cover": None, "source": "default",
        "static_cover_image_path": "", "time_marks": None,
        "tracks": tracks,
        "uneven_animation_template_info": None,
        "update_time": 0, "version": VERSION,
    }

    draft_meta = {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "", "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg", "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "",
                                  "draft_enterprise_id": "",
                                  "draft_enterprise_name": ""},
        "draft_fold_path": str(folder),
        "draft_id": _uid(),
        "draft_is_ae_produce": False, "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False, "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false", "draft_is_invisible": False,
        "draft_materials": [
            {"type": 0, "value": [{
                "create_time": now_s, "duration": video.duration_us,
                "extra_info": video.name, "file_Path": str(video.path),
                "height": video.height, "id": local_id,
                "import_time": now_s, "import_time_ms": now_us,
                "item_source": 1, "md5": "", "metetype": "video",
                "roughcut_time_range": {"duration": video.duration_us,
                                        "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0, "width": video.width,
            }]},
            {"type": 1, "value": []},
        ],
        "draft_name": name,
        "draft_need_rename_folder": False, "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(root),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "", "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_create": now_us, "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total,  # ★ draft_info.duration 과 동일
    }

    virtual_store = {
        "draft_materials": [],
        "draft_virtual_store": [
            {"type": 0, "value": [{"creation_time": 0, "display_name": "",
                                   "filter_type": 0, "id": "",
                                   "import_time": 0, "import_time_us": 0,
                                   "sort_sub_type": 0, "sort_type": 0}]},
            {"type": 1, "value": [{"child_id": local_id, "parent_id": ""}]},
            {"type": 2, "value": []},
        ],
    }

    _write(folder / "draft_info.json", draft_info)
    _write(folder / "draft_meta_info.json", draft_meta)
    _write(folder / "draft_virtual_store.json", virtual_store)
    _write(folder / "draft_agency_config.json",
           {"is_auto_agency_enabled": False, "is_auto_agency_popup": False,
            "is_single_agency_mode": False, "marterials": None,
            "use_converter": False, "video_resolution": 720})
    (folder / "draft_biz_config.json").write_text("")
    (folder / "draft_settings").write_text(
        f"[General]\ndraft_create_time={now_s}\n"
        f"draft_last_edit_time={now_s}\nreal_edit_keys=0\nreal_edit_seconds=0\n")
    for sub in ("Resources", "adjust_mask", "common_attachment", "matting",
                "qr_upload", "smart_crop"):
        (folder / sub).mkdir(exist_ok=True)

    _write_cover(folder, video)
    return folder


def _write_cover(folder: Path, video: VideoInfo) -> None:
    """draft_cover.jpg 를 만든다.

    ★ 커버가 없으면 CapCut 프로젝트 목록에서 '빈 검은 타일' 로 떠서 눈에 안 띈다.
      draft_meta 는 항상 'draft_cover.jpg' 를 참조하므로 파일이 없으면 깨진다.
      영상 앞부분 한 프레임을 뽑아 채운다.
    """
    ts = min(1.0, video.duration_us / US / 2)   # 첫 1초, 아주 짧으면 절반 지점
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}",
             "-i", str(video.path), "-vframes", "1",
             "-vf", "scale=750:-1", str(folder / "draft_cover.jpg")],
            check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass   # 커버는 편의 기능 — 실패해도 드래프트 자체는 유효하다


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
