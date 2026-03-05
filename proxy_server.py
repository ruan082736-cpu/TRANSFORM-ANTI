"""
TRANSFORM - 네이버 뉴스 API 프록시 서버
포트: 5501
"""

from flask import Flask, request, jsonify, send_from_directory, send_file
import requests
import traceback
import urllib.parse
import random
import os
import uuid
import base64
import subprocess
import zipfile
import json as json_mod
import re
import struct
import time

# ffmpeg 경로 (imageio-ffmpeg 패키지)
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_PATH = 'ffmpeg'

app = Flask(__name__, static_folder='.')

NAVER_CLIENT_ID     = '173Fw2_Gev1RH9i9Fr8B'
NAVER_CLIENT_SECRET = 'tU3KhkU9aj'
NAVER_HEADERS = {
    'X-Naver-Client-Id':     NAVER_CLIENT_ID,
    'X-Naver-Client-Secret': NAVER_CLIENT_SECRET,
}

GOOGLE_API_KEY = 'AIzaSyA-R0hSxPTF_C8gtTKBjePIOK74cg5SPwQ'
# 통합 멀티모달 모델 설정 (현재 활성화된 모델: gemini-2.0-flash)
AI_MODEL = 'gemini-2.0-flash'
BASE_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent?key={GOOGLE_API_KEY}'

# 폴백용 모델 설정 (실제 목록에 존재하는 imagen-4.0 사용)
FALLBACK_IMAGE_MODEL = 'imagen-4.0-generate-001'
FALLBACK_IMAGE_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{FALLBACK_IMAGE_MODEL}:predict?key={GOOGLE_API_KEY}'

# TTS 모델 설정
TTS_MODEL = 'gemini-2.5-flash-preview-tts'
TTS_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent?key={GOOGLE_API_KEY}'

# 출력 디렉토리
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

FALLBACK_TREND_KEYWORDS = [
    "WBC 대표팀", "AI 데이터센터", "주식 시장 급등",
    "국회 본회의", "환율 변동"
]

@app.route('/api/trending')
def get_trending():
    # 항상 정확히 5개 반환 보장
    try:
        import json as _json
        query = "속보"
        # 중복 제거를 위해 넉넉히 20개를 가져온 후 필터링
        url = f"https://openapi.naver.com/v1/search/news.json?query={query}&display=20&sort=sim"
        resp = requests.get(url, headers=NAVER_HEADERS, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            items = data.get('items', [])
            raw_titles = [
                item.get('title', '')
                    .replace('<b>', '').replace('</b>', '')
                    .replace('&quot;', '"').replace('&amp;', '&').strip()
                for item in items
            ]

            # 중복 제목 사전 제거 (앞 10글자 기준), 최대 10개 수집
            seen_prefixes = set()
            unique_titles = []
            for t in raw_titles:
                if not t:
                    continue
                prefix = t[:10]
                if prefix not in seen_prefixes:
                    seen_prefixes.add(prefix)
                    unique_titles.append(t)
                if len(unique_titles) >= 10:
                    break

            if unique_titles:
                # Gemini에 보낼 제목은 최대 10개 (5개 보장을 위해 여유 있게)
                titles_for_gemini = unique_titles[:10]
                prompt = (
                    "다음 뉴스 제목들을 읽고, 각 제목의 핵심 내용을 2~5단어의 짧은 한국어 문구로 요약해줘.\n"
                    "규칙:\n"
                    f"1. 정확히 {len(titles_for_gemini)}개의 요약을 반환 (입력 제목 수와 동일)\n"
                    "2. 각 요약은 반드시 12자 이내로 아주 짧게\n"
                    "3. 서로 중복되지 않는 고유한 내용\n"
                    "4. 핵심 명사나 동사 위주로 간결하게\n"
                    "5. 결과는 오직 JSON 문자열 배열 형식으로만: [\"요약1\", \"요약2\", ...]\n\n"
                    f"뉴스 제목들:\n{_json.dumps(titles_for_gemini, ensure_ascii=False)}"
                )
                gemini_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}'
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"}
                }
                g_resp = requests.post(gemini_url, json=payload, timeout=15)
                if g_resp.status_code == 200:
                    try:
                        output = g_resp.json()['candidates'][0]['content']['parts'][0]['text']
                        keywords = _json.loads(output)
                        if isinstance(keywords, list):
                            # 중복 제거
                            seen_kw = set()
                            final_keywords = []
                            for kw in keywords:
                                val = str(list(kw.values())[0]) if isinstance(kw, dict) else str(kw)
                                val = val.strip()
                                if val and val[:4] not in seen_kw:
                                    seen_kw.add(val[:4])
                                    final_keywords.append(val)

                            # 5개 미만이면 원본 뉴스 제목으로 보충
                            for t in unique_titles:
                                if len(final_keywords) >= 5:
                                    break
                                if t[:4] not in seen_kw:
                                    seen_kw.add(t[:4])
                                    final_keywords.append(t)

                            # 그래도 부족하면 fallback으로 보충
                            for fb in FALLBACK_TREND_KEYWORDS:
                                if len(final_keywords) >= 5:
                                    break
                                if fb[:4] not in seen_kw:
                                    seen_kw.add(fb[:4])
                                    final_keywords.append(fb)

                            result = final_keywords[:5]
                            print(f"[DEBUG] 실시간 트렌드 (5개 고정): {result}")
                            return jsonify({'keywords': result})
                    except Exception as parse_err:
                        print(f"[WARN] Gemini JSON 파싱 실패: {parse_err}")

                # Gemini 실패 시: 뉴스 제목 + fallback으로 5개 채움
                combined = unique_titles[:5]
                for fb in FALLBACK_TREND_KEYWORDS:
                    if len(combined) >= 5:
                        break
                    combined.append(fb)
                return jsonify({'keywords': combined[:5]})

    except Exception as e:
        print(f"[ERROR] Trending Load Error: {str(e)}")

    # 네이버 API 자체 실패 시 fallback 5개 반환
    return jsonify({'keywords': FALLBACK_TREND_KEYWORDS[:5]})

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/api/news')
def search_news():
    query   = request.args.get('query', '뉴스')
    display = min(int(request.args.get('display', 10)), 20)
    sort    = request.args.get('sort', 'date')
    url = f"https://openapi.naver.com/v1/search/news.json?query={query}&display={display}&sort={sort}"
    try:
        resp = requests.get(url, headers=NAVER_HEADERS)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/create-card-image', methods=['GET', 'POST', 'OPTIONS'])
def call_imagen_proxy():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    
    if request.method == 'GET':
        return jsonify({'error': 'Please use POST method'}), 400

    print("\n" + "="*50)
    print("[DEBUG] 이미지 생성 요청")
    
    data = request.json
    prompt = data.get('prompt', 'Professional news background')
    aspect_ratio = data.get('aspectRatio', '1:1')

    # 1. 이미지 생성 특화 멀티모달 모델 시도
    GEN_MODEL = 'gemini-2.0-flash-exp-image-generation'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEN_MODEL}:generateContent?key={GOOGLE_API_KEY}'
    
    payload = {
        "contents": [{"parts": [{"text": f"Generate a high-quality visual illustration for: {prompt}"}]}],
        "generationConfig": {
            "maxOutputTokens": 2048,
            "temperature": 1.0
        }
    }
    
    try:
        print(f"[DEBUG] 이미지 특화 모델 ({GEN_MODEL}) 호출 시도...")
        resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=90)
        
        if resp.status_code == 200:
            res_data = resp.json()
            candidates = res_data.get('candidates', [])
            if candidates:
                parts = candidates[0].get('content', {}).get('parts', [])
                for part in parts:
                    data = part.get('inlineData', {}).get('data') or part.get('inline_data', {}).get('data')
                    if data:
                        print(f"[DEBUG] {GEN_MODEL} 이미지 추출 성공!")
                        return jsonify({'success': True, 'image_url': f"data:image/png;base64,{data}", 'source': 'gemini-2-image-gen'})

        print(f"[DEBUG] 메인 모델 실패 ({resp.status_code}), {FALLBACK_IMAGE_MODEL}로 폴백...")
        
        # 2. Imagen 4.0 (:predict 방식) - aspectRatio 지원 범위로 변환 후 호출
        # 지원값: 1:1, 9:16, 16:9, 4:3, 3:4  →  4:5는 지원 안 됨 → 3:4로 대체
        RATIO_MAP = {'4:5': '3:4', '1:1': '1:1', '9:16': '9:16', '16:9': '16:9', '4:3': '4:3', '3:4': '3:4'}
        safe_ratio = RATIO_MAP.get(aspect_ratio, '1:1')
        instances_payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": safe_ratio}
        }
        resp_predict = requests.post(FALLBACK_IMAGE_URL, json=instances_payload, headers={'Content-Type': 'application/json'}, timeout=60)
        
        if resp_predict.status_code == 200:
            resp_data = resp_predict.json()
            predictions = resp_data.get('predictions', [])
            if predictions and isinstance(predictions[0], dict):
                image_bytes = predictions[0].get('bytesBase64Encoded')
                if image_bytes:
                    print(f"[DEBUG] Google {FALLBACK_IMAGE_MODEL} 성공!")
                    return jsonify({'success': True, 'image_url': f"data:image/png;base64,{image_bytes}", 'source': 'imagen-4.0'})
        else:
             print(f"[DEBUG] Predict API 실패 ({resp_predict.status_code}) - {resp_predict.text}")

    except Exception as e:
        print(f"[WARN] Google API 통합 실패, 에러: {str(e)}")
        print(traceback.format_exc())

    # ── 최종 폴백: Pollinations AI (서버에서 직접 가져와 base64로 반환 → canvas crossOrigin 문제 방지) ──
    status_info = f"상태코드: {resp.status_code}" if 'resp' in locals() else "예외 발생"
    print(f"[DEBUG] Google API 실패 원인({status_info}). 서버측 폴백 시도")
    
    sanitized = "".join(c for c in prompt if c.isalnum() or c.isspace()).strip()
    short_prompt = " ".join(sanitized.split()[:30])
    encoded_prompt = urllib.parse.quote(short_prompt)
    seed = random.randint(0, 99999)
    fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
    print(f"[DEBUG] 폴백 URL: {fallback_url}")

    try:
        img_resp = requests.get(fallback_url, timeout=30)
        if img_resp.status_code == 200:
            import base64
            b64 = base64.b64encode(img_resp.content).decode('utf-8')
            mime = img_resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
            print(f"[DEBUG] Pollinations 이미지 서버 다운로드 성공 ({len(img_resp.content)} bytes)")
            return jsonify({"success": True, "image_url": f"data:{mime};base64,{b64}"})
    except Exception as pe:
        print(f"[WARN] Pollinations 다운로드 실패: {pe}")

    return jsonify({"success": True, "image_url": fallback_url})
    
@app.route('/api/gemini', methods=['POST'])
def call_gemini_proxy():
    try:
        data = request.json
        prompt = data.get('prompt')
        is_json = data.get('isJson', False)
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "topP": 0.9,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json" if is_json else "text/plain"
            }
        }
        
        print(f"[DEBUG] 통합 모델 ({AI_MODEL}) 텍스트 호출: {prompt[:50]}...")
        resp = requests.post(BASE_URL, json=payload, headers={'Content-Type': 'application/json'}, timeout=40)
        
        if resp.status_code == 200:
            print(f"[DEBUG] {AI_MODEL} API Proxy Success")
            return jsonify(resp.json()), 200
        
        # 429 한도 초과 시 → gemini-2.5-flash로 즉시 폴백
        if resp.status_code == 429:
            print(f"[WARN] {AI_MODEL} 한도 초과(429), gemini-2.5-flash로 폴백...")
            fallback_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}'
            resp2 = requests.post(fallback_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=40)
            if resp2.status_code == 200:
                print(f"[DEBUG] gemini-2.5-flash 폴백 성공")
                return jsonify(resp2.json()), 200
            print(f"[ERROR] 폴백도 실패: {resp2.status_code} - {resp2.text}")
            return jsonify(resp2.json()), resp2.status_code
        
        print(f"[ERROR] {AI_MODEL} API Failed: {resp.status_code} - {resp.text}")
        return jsonify(resp.json()), resp.status_code
            
    except Exception as e:
        print(f"[ERROR] Gemini Proxy Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ════════════════════════════════════════════
#  숏폼 영상 제작 관련 유틸리티
# ════════════════════════════════════════════

def generate_tts(text, voice_name='Zephyr'):
    """Gemini TTS API로 음성 생성, (audio_bytes, mime_type) 반환"""
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": voice_name
                    }
                }
            }
        }
    }
    try:
        resp = requests.post(TTS_URL, json=payload, headers={'Content-Type': 'application/json'}, timeout=90)
        if resp.status_code == 200:
            data = resp.json()
            parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
            if parts:
                inline_data = parts[0].get('inlineData', {})
                audio_b64 = inline_data.get('data', '')
                mime_type = inline_data.get('mimeType', 'audio/wav')
                if audio_b64:
                    return base64.b64decode(audio_b64), mime_type
        else:
            print(f"[TTS] API 오류: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"[TTS] 예외: {e}")
    return None, None


def save_audio_as_wav(audio_bytes, mime_type, output_path):
    """오디오 데이터를 WAV 파일로 저장 (PCM 변환 포함)"""
    if not audio_bytes:
        return False
    
    if 'wav' in mime_type.lower():
        with open(output_path, 'wb') as f:
            f.write(audio_bytes)
        return True
    elif 'L16' in mime_type or 'pcm' in mime_type.lower():
        # Raw PCM → WAV 헤더 추가
        rate = 24000
        if 'rate=' in mime_type:
            try:
                rate = int(mime_type.split('rate=')[1].split(';')[0].split(',')[0])
            except:
                pass
        num_channels = 1
        sample_width = 2
        data_size = len(audio_bytes)
        with open(output_path, 'wb') as f:
            f.write(b'RIFF')
            f.write(struct.pack('<I', 36 + data_size))
            f.write(b'WAVE')
            f.write(b'fmt ')
            f.write(struct.pack('<I', 16))
            f.write(struct.pack('<H', 1))
            f.write(struct.pack('<H', num_channels))
            f.write(struct.pack('<I', rate))
            f.write(struct.pack('<I', rate * num_channels * sample_width))
            f.write(struct.pack('<HH', num_channels * sample_width, sample_width * 8))
            f.write(b'data')
            f.write(struct.pack('<I', data_size))
            f.write(audio_bytes)
        return True
    else:
        # 기타 포맷 → ffmpeg로 변환
        temp_path = output_path + '.tmp'
        with open(temp_path, 'wb') as f:
            f.write(audio_bytes)
        try:
            subprocess.run([FFMPEG_PATH, '-y', '-i', temp_path, '-ar', '24000', '-ac', '1', output_path],
                          capture_output=True, timeout=15)
            os.remove(temp_path)
            return True
        except:
            if os.path.exists(temp_path):
                os.rename(temp_path, output_path)
            return True


def get_audio_duration(filepath):
    """오디오 파일 길이(초) 측정 (ffprobe 사용)"""
    try:
        ffprobe = FFMPEG_PATH.replace('ffmpeg', 'ffprobe') if 'ffmpeg' in FFMPEG_PATH else 'ffprobe'
        # ffprobe가 없으면 ffmpeg -i 로 대체
        result = subprocess.run(
            [FFMPEG_PATH, '-i', filepath, '-f', 'null', '-'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stderr.split('\n'):
            if 'Duration' in line:
                time_str = line.split('Duration:')[1].split(',')[0].strip()
                parts = time_str.split(':')
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except:
        pass
    return 5.0


def generate_image_for_sentence(sentence, style_name='실사'):
    """문장에 맞는 9:16 이미지 생성 (bytes 반환) - 다단계 폴백"""
    style_map = {
        '실사': 'Photorealistic', '카툰': 'Cartoon style',
        '수채화': 'Watercolor painting', '픽셀아트': 'Pixel art'
    }
    style_eng = style_map.get(style_name, 'Photorealistic')
    
    # Gemini로 영문 시각 묘사 생성
    prompt_gen = f"""Create a concise English image description for this Korean news sentence.
Output ONLY the English visual description (max 50 words), no labels.
Include Korean people with East Asian appearance if people are involved.
Sentence: {sentence}"""
    
    gemini_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}'
    visual_desc = "Professional news illustration"
    try:
        resp = requests.post(gemini_url, json={
            "contents": [{"parts": [{"text": prompt_gen}]}],
            "generationConfig": {"maxOutputTokens": 150}
        }, timeout=15)
        if resp.status_code == 200:
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            visual_desc = re.sub(r'[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]', '', raw).strip() or visual_desc
    except:
        pass
    
    final_prompt = f"{style_eng} style, {visual_desc}, vertical portrait, Korean characters, professional high resolution, cinematic"
    print(f"    이미지 프롬프트: {final_prompt[:100]}...")
    
    # ── 1차: Gemini 이미지 생성 모델 (responseModalities 포함) ──
    GEN_MODEL = 'gemini-2.0-flash-exp-image-generation'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEN_MODEL}:generateContent?key={GOOGLE_API_KEY}'
    try:
        resp = requests.post(url, json={
            "contents": [{"parts": [{"text": f"Generate a high-quality visual illustration: {final_prompt}"}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "maxOutputTokens": 2048,
                "temperature": 1.0
            }
        }, timeout=90)
        print(f"    [1차 Gemini] 상태: {resp.status_code}")
        if resp.status_code == 200:
            res_data = resp.json()
            candidates = res_data.get('candidates', [])
            if candidates:
                parts = candidates[0].get('content', {}).get('parts', [])
                for part in parts:
                    inline = part.get('inlineData') or part.get('inline_data')
                    if inline and inline.get('data'):
                        print(f"    [1차 Gemini] ✓ 이미지 추출 성공!")
                        return base64.b64decode(inline['data'])
                # 이미지가 없는 경우 원인 로깅
                finish_reason = candidates[0].get('finishReason', 'UNKNOWN')
                print(f"    [1차 Gemini] 이미지 없음 (finishReason: {finish_reason}, parts: {len(parts)})")
            else:
                print(f"    [1차 Gemini] candidates 없음")
        else:
            err_text = resp.text[:200] if resp.text else 'no body'
            print(f"    [1차 Gemini] HTTP {resp.status_code}: {err_text}")
    except Exception as e:
        print(f"    [1차 Gemini] 예외: {e}")
    
    # ── 2차: Imagen 4.0 (predict 방식) ──
    try:
        clean_prompt = re.sub(r'[^a-zA-Z0-9 ,.]', '', final_prompt).strip()[:500]
        imagen_payload = {
            "instances": [{"prompt": clean_prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": "9:16"}
        }
        resp2 = requests.post(FALLBACK_IMAGE_URL, json=imagen_payload,
                             headers={'Content-Type': 'application/json'}, timeout=60)
        print(f"    [2차 Imagen] 상태: {resp2.status_code}")
        if resp2.status_code == 200:
            predictions = resp2.json().get('predictions', [])
            if predictions and isinstance(predictions[0], dict):
                img_b64 = predictions[0].get('bytesBase64Encoded')
                if img_b64:
                    print(f"    [2차 Imagen] ✓ 이미지 추출 성공!")
                    return base64.b64decode(img_b64)
        else:
            print(f"    [2차 Imagen] 실패: {resp2.text[:150]}")
    except Exception as e:
        print(f"    [2차 Imagen] 예외: {e}")
    
    # ── 3차: Pollinations AI (서버에서 직접 다운로드) ──
    try:
        sanitized_words = re.sub(r'[^a-zA-Z0-9 ]', '', final_prompt).split()[:20]
        poll_prompt = ' '.join(sanitized_words) or 'professional news background vertical'
        seed = random.randint(0, 99999)
        fallback_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(poll_prompt)}?width=1080&height=1920&nologo=true&seed={seed}"
        print(f"    [3차 Pollinations] URL 호출 중...")
        img_resp = requests.get(fallback_url, timeout=45, allow_redirects=True)
        print(f"    [3차 Pollinations] 상태: {img_resp.status_code}, 크기: {len(img_resp.content)} bytes")
        if img_resp.status_code == 200 and len(img_resp.content) > 1000:
            content_type = img_resp.headers.get('Content-Type', '')
            if 'image' in content_type or len(img_resp.content) > 5000:
                print(f"    [3차 Pollinations] ✓ 이미지 다운로드 성공!")
                return img_resp.content
    except Exception as e:
        print(f"    [3차 Pollinations] 예외: {e}")
    
    print(f"    ⚠️ 모든 이미지 생성 방법 실패")
    return None


@app.route('/api/shortform/generate', methods=['POST', 'OPTIONS'])
def generate_shortform():
    """숏폼 영상 제작 전체 파이프라인"""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    
    data = request.json
    article_text = data.get('text', '')
    voice_name = data.get('voice', 'Zephyr')
    style_name = data.get('style', '실사')
    length_sec = data.get('length', '15')
    
    if not article_text.strip():
        return jsonify({'error': '기사 내용이 없습니다'}), 400
    
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(OUTPUT_DIR, f'short_{job_id}')
    os.makedirs(job_dir, exist_ok=True)
    
    try:
        print(f"\n{'='*50}")
        print(f"[숏폼] 제작 시작 (Job: {job_id}, 길이: {length_sec}초)")
        
        # 길이에 따른 조건 설정
        if length_sec == '30':
            sentence_cnt = "6~7"
            char_cnt = "160~240"
            max_sentences = 7
        elif length_sec == '20':
            sentence_cnt = "4~5"
            char_cnt = "100~140"
            max_sentences = 5
        else: # 기본 15초
            length_sec = '15'
            sentence_cnt = "3~4"
            char_cnt = "80~120"
            max_sentences = 4

        # ── Step 1: 대본 생성 ──
        print(f"[숏폼] Step 1/5: 대본 생성 중 ({length_sec}초)...")
        script_prompt = f"""다음 기사를 {length_sec}초 분량의 숏폼 영상 대본으로 변환해주세요.
규칙:
1. 정확히 {sentence_cnt}문장으로 구성
2. 전체 읽는 시간이 약 {length_sec}초가 되도록 (총 {char_cnt}자 내외)
3. 각 문장은 간결하고 임팩트 있게 (뉴스 앵커 말투)
4. 첫 문장은 시청자를 끌어들이는 후킹
5. 마지막 문장은 핵심 결론
결과를 JSON으로 반환:
{{"sentences": ["문장1", "문장2", "문장3"]}}

기사:
{article_text[:2000]}"""
        
        gemini_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}'
        resp = requests.post(gemini_url, json={
            "contents": [{"parts": [{"text": script_prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }, timeout=20)
        
        sentences = []
        if resp.status_code == 200:
            try:
                text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                script_data = json_mod.loads(text)
                sentences = script_data.get('sentences', [])
            except Exception as e:
                print(f"[숏폼] 대본 파싱 오류: {e}")
        
        if not sentences:
            sentences = [s.strip() + '.' for s in article_text.split('.') if s.strip()][:3]
        
        # 최대 문장수 제한
        sentences = sentences[:max_sentences]
        print(f"[숏폼] 대본 ({len(sentences)}문장): {sentences}")
        
        # ── Step 2: 문장별 TTS 음성 생성 ──
        print("[숏폼] Step 2/5: TTS 음성 생성 중...")
        audio_files = []
        durations = []
        for i, sentence in enumerate(sentences):
            print(f"  - 문장 {i+1}/{len(sentences)} TTS: {sentence[:30]}...")
            audio_bytes, mime_type = generate_tts(sentence, voice_name)
            if audio_bytes:
                audio_path = os.path.join(job_dir, f'news_{i+1:02d}.wav')
                save_audio_as_wav(audio_bytes, mime_type, audio_path)
                dur = get_audio_duration(audio_path)
                audio_files.append(audio_path)
                durations.append(dur)
                print(f"    ✓ 저장 완료: {len(audio_bytes)} bytes, {dur:.2f}s")
            else:
                print(f"    ✗ TTS 생성 실패")
        
        if not audio_files:
            return jsonify({'error': 'TTS 음성 생성에 실패했습니다. 잠시 후 다시 시도해주세요.'}), 500
        
        # ── Step 3: 문장별 이미지 생성 ──
        print("[숏폼] Step 3/5: 이미지 생성 중...")
        image_files = []
        for i, sentence in enumerate(sentences):
            print(f"  - 문장 {i+1}/{len(sentences)} 이미지 생성...")
            img_bytes = generate_image_for_sentence(sentence, style_name)
            if img_bytes:
                img_path = os.path.join(job_dir, f'news_{i+1:02d}.png')
                with open(img_path, 'wb') as f:
                    f.write(img_bytes)
                image_files.append(img_path)
                print(f"    ✓ 이미지 저장: {len(img_bytes)} bytes")
            else:
                print(f"    ✗ 이미지 생성 실패")
        
        # 이미지 부족 시 마지막 이미지로 채우기
        while len(image_files) < len(audio_files):
            if image_files:
                image_files.append(image_files[-1])
            else:
                return jsonify({'error': '이미지 생성에 실패했습니다'}), 500
        
        # ── Step 4: 오디오 결합 ──
        print("[숏폼] Step 4/5: 오디오 결합 중...")
        combined_audio = os.path.join(job_dir, 'news_combined.wav')
        if len(audio_files) > 1:
            filter_parts = ''.join([f'[{i}:a]' for i in range(len(audio_files))])
            concat_filter = f'{filter_parts}concat=n={len(audio_files)}:v=0:a=1[out]'
            cmd = [FFMPEG_PATH, '-y']
            for af in audio_files:
                cmd.extend(['-i', af])
            cmd.extend(['-filter_complex', concat_filter, '-map', '[out]', combined_audio])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                print(f"[숏폼] 오디오 결합 실패: {result.stderr[:200]}")
                # 폴백: 첫 번째 파일만 사용
                import shutil
                shutil.copy2(audio_files[0], combined_audio)
                durations = [durations[0]] * len(image_files)
        else:
            import shutil
            shutil.copy2(audio_files[0], combined_audio)
        
        total_duration = sum(durations)
        print(f"  총 오디오 길이: {total_duration:.2f}s")
        
        # ── Step 5: ffmpeg로 영상 합성 ──
        print("[숏폼] Step 5/5: 영상 합성 중 (ffmpeg)...")
        
        # concat 파일 생성 (이미지 + 표시 시간)
        concat_file = os.path.join(job_dir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for i in range(len(image_files)):
                safe_path = image_files[i].replace('\\', '/')
                dur = durations[i] if i < len(durations) else durations[-1]
                f.write(f"file '{safe_path}'\n")
                f.write(f"duration {dur}\n")
            # concat demuxer 마지막 프레임 유지를 위해 마지막 이미지 반복
            safe_path = image_files[-1].replace('\\', '/')
            f.write(f"file '{safe_path}'\n")
        
        output_mp4 = os.path.join(job_dir, f'news_{job_id}.mp4')
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'concat', '-safe', '0', '-i', concat_file,
            '-i', combined_audio,
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
            '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-r', '30',
            '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            output_mp4
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[숏폼] ffmpeg 오류: {result.stderr[:500]}")
            return jsonify({'error': f'영상 합성 실패: {result.stderr[:200]}'}), 500
        
        print(f"[숏폼] ✓ 영상 생성 완료: {output_mp4}")
        
        # ── ZIP 번들 생성 ──
        zip_filename = 'short_2026.zip'
        zip_path = os.path.join(job_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for img_path in image_files:
                zf.write(img_path, os.path.basename(img_path))
            for af in audio_files:
                zf.write(af, os.path.basename(af))
            zf.write(combined_audio, 'news_combined.wav')
            zf.write(output_mp4, os.path.basename(output_mp4))
            # 대본 JSON
            script_json_path = os.path.join(job_dir, 'script.json')
            with open(script_json_path, 'w', encoding='utf-8') as sf:
                json_mod.dump({'sentences': sentences, 'durations': durations, 'total_duration': total_duration}, sf, ensure_ascii=False, indent=2)
            zf.write(script_json_path, 'script.json')
        
        print(f"[숏폼] ✓ ZIP 생성 완료: {zip_path}")
        print(f"[숏폼] === 제작 완료 (Job: {job_id}) ===")
        
        # 결과 반환 (영상은 URL로 제공)
        return jsonify({
            'success': True,
            'job_id': job_id,
            'sentences': sentences,
            'durations': durations,
            'total_duration': total_duration,
            'video_url': f'/output/short_{job_id}/{os.path.basename(output_mp4)}',
            'download_url': f'/output/short_{job_id}/{zip_filename}',
            'image_count': len(image_files),
            'images': [f'/output/short_{job_id}/news_{i+1:02d}.png' for i in range(len(image_files))]
        })
        
    except Exception as e:
        print(f"[숏폼] 오류: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

if __name__ == '__main__':
    print('\n' + '★'*50)
    print(' TRANSFORM Proxy Server v2.0.0 (숏폼 지원) ')
    print(f' ffmpeg: {FFMPEG_PATH}')
    print(' API Endpoints:')
    print('   http://localhost:5501/api/shortform/generate')
    print('   http://localhost:5501/api/create-card-image')
    print('★'*50 + '\n')
    app.run(port=5501, debug=False)
