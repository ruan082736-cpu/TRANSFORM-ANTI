
import os
import re

filepath = r'c:\Users\PC\Desktop\2026-01-19\29.03.09(1010)\index.html'

# Read as UTF-8 (original file is clean now)
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Define the new searchNews function with Lock and De-duplication
new_code = """        let currentQuery = '';
        let currentStart = 1;
        let currentItems = [];
        let isNewsSearching = false;

        async function searchNews(query, append = false) {
            if (!query.trim() || isNewsSearching) return;

            const list = document.getElementById('newsList');
            isNewsSearching = true;

            if (!append) {
                currentQuery = query;
                currentStart = 1;
                currentItems = [];
                list.innerHTML = '<div class="spinner"></div>';
            } else {
                const spinner = document.createElement('div');
                spinner.className = 'spinner';
                spinner.id = 'appendSpinner';
                list.appendChild(spinner);
            }

            try {
                if (currentStart > 1000) {
                    isNewsSearching = false;
                    return;
                }

                const res = await fetch(`${API}/api/news?query=${encodeURIComponent(query)}&display=5&sort=date&start=${currentStart}`);
                const data = await res.json();
                const items = data.items || [];

                if (append) {
                    const sp = document.getElementById('appendSpinner');
                    if (sp) sp.remove();
                }

                if (!items.length && !append) {
                    list.innerHTML = '<div class="empty-state">검색 결과가 없습니다.</div>';
                    isNewsSearching = false;
                    return;
                }

                // 중복 제거 (링크 기준)
                const newItems = items.filter(newItem => {
                    const newLink = newItem.link || newItem.originallink;
                    return !currentItems.some(oldItem => (oldItem.link || oldItem.originallink) === newLink);
                });

                currentItems = append ? [...currentItems, ...newItems] : items;
                currentStart += items.length;

                // 카드 렌더링
                const cardsHtml = currentItems.map((item, idx) => {
                    const title = stripHtml(item.title);
                    const desc = stripHtml(item.description);
                    const pubDate = formatDate(item.pubDate);
                    return `
                    <div class="news-card" data-idx="${idx}">
                        <div class="news-card-source">${pubDate}</div>
                        <div class="news-title">${title}</div>
                        <div class="news-summary">${desc}</div>
                        <a class="news-apply" href="#" data-idx="${idx}">본문 바로 적용</a>
                    </div>
                `;
                }).join('');

                const moreBtn = (data.total > currentStart && currentStart <= 1000)
                    ? `<button class="btn-load-more" id="loadMoreBtn">더보기(총 ${Number(data.total).toLocaleString()}건)</button>`
                    : '';

                list.innerHTML = cardsHtml + moreBtn;

                // 이벤트 재설업
                list.querySelectorAll('.news-card').forEach(card => {
                    card.addEventListener('click', (e) => {
                        if (e.target.classList.contains('news-apply')) return;
                        const idx = parseInt(card.dataset.idx);
                        openModal(currentItems[idx]);
                    });
                });

                list.querySelectorAll('.news-apply').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        const idx = parseInt(btn.dataset.idx);
                        applyToEditor(currentItems[idx]);
                    });
                });

                const loadMoreBtn = document.getElementById('loadMoreBtn');
                if (loadMoreBtn) {
                    loadMoreBtn.addEventListener('click', () => searchNews(currentQuery, true));
                }

            } catch (e) {
                console.error(e);
                list.innerHTML = '<div class="empty-state">⚠️ 검색 실패<br><small>프록시 서버 상태를 확인하세요</small></div>';
            } finally {
                isNewsSearching = false;
            }
        }"""

# Pattern to find: from let currentQuery to the end of catch/finally block if exists, or just the next long banner
# In index.html, it's followed by a ═ banner.
pattern = r'let\s+currentQuery\s*=\s*.+?async\s+function\s+searchNews.+?finally\s*\{.+?isNewsSearching\s*=\s*false;\s*\}\s*\}'
# Actually, the original doesn't have finally.
pattern = r'let\s+currentQuery\s*=\s*.+?async\s+function\s+searchNews.+?catch\s*\(e\)\s*\{.+?\}\s*\}'

# Let's use a simpler search based on known headers
start_idx = content.find("let currentQuery =")
modal_idx = content.find("function openModal")
if start_idx != -1 and modal_idx != -1:
    end_idx = content.rfind("}", 0, modal_idx) + 1
    fixed_content = content[:start_idx] + new_code + content[end_idx:]
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("SUCCESS")
else:
    print(f"NOT FOUND: start_idx={start_idx}, modal_idx={modal_idx}")
