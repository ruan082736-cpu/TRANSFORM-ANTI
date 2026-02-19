"""
TRANSFORM - 네이버 뉴스 API 프록시 서버
포트: 5501
"""

from flask import Flask, request, jsonify, send_from_directory
import requests
import concurrent.futures
import os

app = Flask(__name__, static_folder='.')

NAVER_CLIENT_ID     = '173Fw2_Gev1RH9i9Fr8B'
NAVER_CLIENT_SECRET = 'tU3KhkU9aj'
NAVER_HEADERS = {
    'X-Naver-Client-Id':     NAVER_CLIENT_ID,
    'X-Naver-Client-Secret': NAVER_CLIENT_SECRET,
}

# 트렌딩 후보 키워드 (다양한 분야)
TREND_CANDIDATES = [
    '의대', '의료파업', '트럼프', '주식', '코스피', '부동산',
    '삼성전자', '환율', '물가', '금리', '북한', '총선',
    '아이폰', '인공지능', 'AI', '넥슨', '카카오', '현대차',
    '날씨', '올림픽', '야구', '축구', '드라마', '영화'
]


# ── CORS 헤더 ─────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ── 정적 파일 서빙 ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


# ── 뉴스 검색 API ─────────────────────────────────────────
@app.route('/api/news')
def search_news():
    query   = request.args.get('query', '뉴스')
    display = min(int(request.args.get('display', 10)), 20)
    sort    = request.args.get('sort', 'date')
    start   = int(request.args.get('start', 1))

    try:
        resp = requests.get(
            'https://openapi.naver.com/v1/search/news.json',
            params={
                'query':   query,
                'display': display,
                'sort':    sort,
                'start':   start,
            },
            headers=NAVER_HEADERS,
            timeout=10,
        )
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({'error': str(e), 'items': [], 'total': 0}), 500


# ── 트렌딩 키워드 API ─────────────────────────────────────
@app.route('/api/trending')
def get_trending():
    def fetch_count(keyword):
        try:
            resp = requests.get(
                'https://openapi.naver.com/v1/search/news.json',
                params={'query': keyword, 'display': 5, 'sort': 'date'},
                headers=NAVER_HEADERS,
                timeout=8,
            )
            data  = resp.json()
            total = data.get('total', 0)
            items = data.get('items', [])
            return {'keyword': keyword, 'total': total, 'sample': items[:1]}
        except Exception:
            return {'keyword': keyword, 'total': 0, 'sample': []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_count, TREND_CANDIDATES))

    # 뉴스 수 기준 내림차순 정렬 → 상위 5개
    results.sort(key=lambda x: x['total'], reverse=True)
    top5 = results[:5]

    return jsonify({
        'keywords': [r['keyword'] for r in top5],
        'details':  top5,
    })


if __name__ == '__main__':
    print('=' * 50)
    print('  TRANSFORM 프록시 서버 시작 (포트 5501)')
    print('  http://localhost:5501')
    print('=' * 50)
    app.run(port=5501, debug=False)
