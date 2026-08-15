// app.js — Logic chinh cho ChatGPT-style frontend
// Ket noi FastAPI backend qua REST + SSE

(() => {
    'use strict';

    // =========================================================================
    // CONFIG
    // =========================================================================
    const API_BASE = window.location.origin;
    const STORAGE_KEY = 'cmc_chat_sessions';
    const MAX_SESSIONS = 50;
    const llmProviderSelect = document.getElementById('llmProvider');

    // =========================================================================
    // STATE
    // =========================================================================
    let sessions = [];          // [{id, title, messages: [{role, content, timestamp}], createdAt, updatedAt}]
    let activeSessionId = null;
    let isGenerating = false;
    let pendingImageFile = null; // File object when user selects an image

    // Load saved provider
    const savedProvider = localStorage.getItem('llmProvider');
    if (savedProvider && llmProviderSelect) {
        llmProviderSelect.value = savedProvider;
    }
    if (llmProviderSelect) {
        llmProviderSelect.addEventListener('change', () => {
            localStorage.setItem('llmProvider', llmProviderSelect.value);
        });
    }

    // =========================================================================
    // DOM REFS
    // =========================================================================
    const $ = (sel) => document.querySelector(sel);
    const sidebar = $('#sidebar');
    const chatHistory = $('#chatHistory');
    const welcomeScreen = $('#welcomeScreen');
    const messagesEl = $('#messages');
    const chatContainer = $('#chatContainer');
    const messageInput = $('#messageInput');
    const sendBtn = $('#sendBtn');
    const newChatBtn = $('#newChatBtn');
    const closeSidebarBtn = $('#closeSidebarBtn');
    const openSidebarBtn = $('#openSidebarBtn');
    const topbarTitle = $('#topbarTitle');
    const searchInput = $('#searchInput');
    const suggestions = $('#suggestions');
    const uploadImgBtn = $('#uploadImgBtn');
    const imageFileInput = $('#imageFileInput');
    const imagePreviewContainer = $('#imagePreviewContainer');
    const imagePreviewImg = $('#imagePreviewImg');
    const imagePreviewRemove = $('#imagePreviewRemove');

    // =========================================================================
    // LOCAL STORAGE
    // =========================================================================
    function loadSessions() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            sessions = raw ? JSON.parse(raw) : [];
        } catch {
            sessions = [];
        }
    }

    function saveSessions() {
        // Keep only last MAX_SESSIONS
        if (sessions.length > MAX_SESSIONS) {
            sessions = sessions.slice(-MAX_SESSIONS);
        }
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    }

    function getSession(id) {
        return sessions.find(s => s.id === id);
    }

    // =========================================================================
    // SESSION MANAGEMENT
    // =========================================================================
    function createSession() {
        const id = 'chat_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        const session = {
            id,
            title: 'Chat mới',
            messages: [],
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
        };
        sessions.push(session);
        saveSessions();
        return session;
    }

    function deleteSession(id) {
        sessions = sessions.filter(s => s.id !== id);
        saveSessions();
        if (activeSessionId === id) {
            activeSessionId = null;
            showWelcome();
        }
        renderHistory();
    }

    function setActiveSession(id) {
        activeSessionId = id;
        const session = getSession(id);
        if (!session) return;

        topbarTitle.textContent = session.title;
        renderMessages(session.messages);
        renderHistory();

        // Hide welcome, show messages
        welcomeScreen.classList.add('hidden');
        messagesEl.style.display = 'block';
    }

    function showWelcome() {
        activeSessionId = null;
        topbarTitle.textContent = 'Chat mới';
        messagesEl.innerHTML = '';
        messagesEl.style.display = 'none';
        welcomeScreen.classList.remove('hidden');
        renderHistory();
    }

    // =========================================================================
    // MARKDOWN RENDERER (lightweight)
    // =========================================================================
    function renderMarkdown(text) {
        if (!text) return '';
        let html = text;

        // Escape HTML first
        html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        // Code blocks (```)
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre><code>${code.trim()}</code></pre>`;
        });

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Tables (| ... | ... |)
        html = html.replace(/((?:^\|.+\|$\n?)+)/gm, (tableBlock) => {
            const rows = tableBlock.trim().split('\n').filter(r => r.trim());
            if (rows.length < 2) return tableBlock;

            // Check if 2nd row is separator
            const isSeparator = (row) => /^\|[\s\-:|]+\|$/.test(row);
            let headerEnd = isSeparator(rows[1]) ? 1 : 0;

            let tableHtml = '<table>';
            rows.forEach((row, idx) => {
                if (isSeparator(row)) return;
                const cells = row.split('|').filter((_, i, arr) => i > 0 && i < arr.length - 1);
                const tag = idx < headerEnd || (headerEnd === 0 && idx === 0) ? 'th' : 'td';
                const rowTag = tag === 'th' ? 'thead' : (idx === headerEnd + 1 ? 'tbody' : '');
                if (rowTag === 'thead') tableHtml += '<thead>';
                if (rowTag === 'tbody') tableHtml += '</thead><tbody>';
                tableHtml += '<tr>' + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
            });
            tableHtml += '</tbody></table>';
            return tableHtml;
        });

        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Blockquote
        html = html.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');

        // Unordered lists
        html = html.replace(/^[-*]\s+(.+)$/gm, '<li>$1</li>');
        html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

        // Ordered lists
        html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');

        // Horizontal rules
        html = html.replace(/^---$/gm, '<hr>');

        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

        // Headers
        html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

        // Paragraphs — wrap remaining loose text
        html = html.replace(/\n\n/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');

        // Clean up extra <p> tags around block elements
        html = html.replace(/<p>\s*(<(?:h[1-6]|ul|ol|pre|table|blockquote|hr))/g, '$1');
        html = html.replace(/(<\/(?:h[1-6]|ul|ol|pre|table|blockquote|hr)>)\s*<\/p>/g, '$1');

        return `<p>${html}</p>`.replace(/<p>\s*<\/p>/g, '');
    }

    // =========================================================================
    // RENDER MESSAGES
    // =========================================================================
    function renderMessages(messages) {
        messagesEl.innerHTML = '';
        messages.forEach(msg => {
            appendMessageToDOM(msg.role, msg.content);
        });
        scrollToBottom();
    }

    function appendMessageToDOM(role, content, opts = {}) {
        const msgEl = document.createElement('div');
        msgEl.className = `message ${role}`;

        const avatarText = role === 'user' ? 'B' : '🎓';
        const roleName = role === 'user' ? 'Bạn' : 'CMC AI';

        msgEl.innerHTML = `
            <div class="message-avatar">${avatarText}</div>
            <div class="message-content">
                <div class="message-role">${roleName}</div>
                <div class="message-text" id="${opts.textId || ''}">${opts.isStreaming ? '' : renderMarkdown(content)
            }</div>
            </div>
        `;

        messagesEl.appendChild(msgEl);
        scrollToBottom();
        return msgEl;
    }

    function appendTypingIndicator() {
        const el = document.createElement('div');
        el.className = 'message assistant';
        el.id = 'typingIndicator';
        el.innerHTML = `
            <div class="message-avatar">🎓</div>
            <div class="message-content">
                <div class="message-role">CMC AI</div>
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        `;
        messagesEl.appendChild(el);
        scrollToBottom();
    }

    function buildCitationBlock(data) {
        const rag = Array.isArray(data?.rag_citations) ? data.rag_citations : [];
        const web = Array.isArray(data?.web_citations) ? data.web_citations : [];
        const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
        const lines = [];

        if (rag.length > 0) {
            lines.push('', '---', '📎 Nguồn tài liệu nội bộ:');
            rag.forEach(c => {
                const parts = [`[${c.index}] \`${c.source_file}\``];
                if (c.page_number) parts.push(`trang ${c.page_number}`);
                if (c.admission_cycle) parts.push(`kỳ ${c.admission_cycle}`);
                if (c.document_type) parts.push(`(${c.document_type})`);
                lines.push(parts.join(' - '));
            });
        }

        if (web.length > 0) {
            lines.push('', '🌐 Nguồn web:');
            web.forEach(c => {
                const tag = c.is_official ? 'official' : 'external';
                lines.push(`[W${c.index}] ${c.title} - ${c.url} (${tag})`);
            });
        }

        if (warnings.length > 0) {
            lines.push('', '⚠️ Lưu ý:');
            warnings.forEach(w => lines.push(`- ${w}`));
        }
        return lines.join('\n').trim();
    }

    function removeTypingIndicator() {
        const el = document.getElementById('typingIndicator');
        if (el) el.remove();
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            chatContainer.scrollTop = chatContainer.scrollHeight;
        });
    }

    // =========================================================================
    // RENDER SIDEBAR HISTORY
    // =========================================================================
    function renderHistory(filter = '') {
        chatHistory.innerHTML = '';
        const filterLower = filter.toLowerCase();

        // Group by date
        const groups = {};
        const now = new Date();
        const today = now.toDateString();
        const yesterday = new Date(now - 86400000).toDateString();

        const filteredSessions = sessions
            .filter(s => !filterLower || s.title.toLowerCase().includes(filterLower))
            .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));

        filteredSessions.forEach(session => {
            const date = new Date(session.updatedAt);
            const dateStr = date.toDateString();
            let groupLabel;
            if (dateStr === today) groupLabel = 'Hôm nay';
            else if (dateStr === yesterday) groupLabel = 'Hôm qua';
            else if (now - date < 7 * 86400000) groupLabel = '7 ngày trước';
            else groupLabel = 'Cũ hơn';

            if (!groups[groupLabel]) groups[groupLabel] = [];
            groups[groupLabel].push(session);
        });

        Object.entries(groups).forEach(([label, items]) => {
            const groupEl = document.createElement('div');
            groupEl.className = 'chat-history-group';
            groupEl.innerHTML = `<div class="chat-history-group-label">${label}</div>`;

            items.forEach(session => {
                const itemEl = document.createElement('div');
                itemEl.className = `history-item${session.id === activeSessionId ? ' active' : ''}`;
                itemEl.innerHTML = `
                    <span class="history-item-title">${escapeHtml(session.title)}</span>
                    <button class="history-item-delete" title="Xóa">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m2 0v14a2 2 0 01-2 2H8a2 2 0 01-2-2V6h12"/>
                        </svg>
                    </button>
                `;

                itemEl.addEventListener('click', (e) => {
                    if (e.target.closest('.history-item-delete')) {
                        e.stopPropagation();
                        deleteSession(session.id);
                        return;
                    }
                    setActiveSession(session.id);
                });

                groupEl.appendChild(itemEl);
            });

            chatHistory.appendChild(groupEl);
        });

        if (filteredSessions.length === 0 && filter) {
            chatHistory.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:0.8125rem;">Không tìm thấy kết quả</div>';
        }
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // =========================================================================
    // IMAGE UPLOAD HANDLING
    // =========================================================================
    function selectImage(file) {
        if (!file || !file.type.startsWith('image/')) return;
        pendingImageFile = file;

        // Show preview
        const reader = new FileReader();
        reader.onload = (e) => {
            imagePreviewImg.src = e.target.result;
            imagePreviewContainer.classList.add('active');
            uploadImgBtn.classList.add('has-image');
            updateSendButton();
        };
        reader.readAsDataURL(file);
    }

    function clearImage() {
        pendingImageFile = null;
        imagePreviewImg.src = '';
        imagePreviewContainer.classList.remove('active');
        uploadImgBtn.classList.remove('has-image');
        imageFileInput.value = '';
        updateSendButton();
    }

    // =========================================================================
    // SEND MESSAGE (SSE STREAMING)
    // =========================================================================
    async function sendMessage(question) {
        // If there's a pending image, route to image send flow
        if (pendingImageFile) {
            return sendImageMessage(question);
        }

        if (!question.trim() || isGenerating) return;

        isGenerating = true;
        sendBtn.classList.remove('active');
        sendBtn.disabled = true;
        messageInput.disabled = true;
        messageInput.placeholder = '⏳ Đang tạo câu trả lời...';

        // Ensure we have an active session
        let session;
        if (!activeSessionId) {
            session = createSession();
            activeSessionId = session.id;
            welcomeScreen.classList.add('hidden');
            messagesEl.style.display = 'block';
            messagesEl.innerHTML = '';
        } else {
            session = getSession(activeSessionId);
        }

        // Update title from first message
        if (session.messages.length === 0) {
            session.title = question.length > 40
                ? question.slice(0, 40) + '...'
                : question;
        }

        // Add user message
        session.messages.push({
            role: 'user',
            content: question,
            timestamp: new Date().toISOString(),
        });
        session.updatedAt = new Date().toISOString();
        saveSessions();

        appendMessageToDOM('user', question);
        renderHistory();
        topbarTitle.textContent = session.title;

        // Show typing indicator
        appendTypingIndicator();

        // Stream response via fetch (SSE)
        let fullText = '';
        const textId = 'stream_' + Date.now();

        try {
            const providerSelect = document.getElementById('llmProvider');
            const provider = providerSelect ? providerSelect.value : 'openrouter';

            const response = await fetch(`${API_BASE}/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, provider }),
            });

            if (!response.ok) {
                const err = await response.json().catch(() => ({ detail: 'Lỗi kết nối' }));
                throw new Error(err.detail || `HTTP ${response.status}`);
            }

            removeTypingIndicator();

            // Create assistant message element
            const msgEl = appendMessageToDOM('assistant', '', { textId, isStreaming: true });
            const textEl = msgEl.querySelector(`#${textId}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const jsonStr = line.slice(6).trim();
                    if (!jsonStr) continue;

                    try {
                        const data = JSON.parse(jsonStr);

                        if (data.error) {
                            fullText += `\n\n❌ Lỗi: ${data.error}`;
                            textEl.innerHTML = renderMarkdown(fullText);
                            scrollToBottom();
                            continue;
                        }

                        if (data.token) {
                            fullText += data.token;
                            textEl.innerHTML = renderMarkdown(fullText) + '<span class="cursor-blink">▌</span>';
                            scrollToBottom();
                        }

                        if (data.done) {
                            // Use full answer from server (includes citations)
                            if (data.full_answer) {
                                fullText = data.full_answer;
                            } else {
                                const citationBlock = buildCitationBlock(data);
                                if (citationBlock) {
                                    fullText = `${fullText}\n\n${citationBlock}`.trim();
                                }
                            }
                            textEl.innerHTML = renderMarkdown(fullText);
                            scrollToBottom();
                        }
                    } catch {
                        // Skip malformed JSON
                    }
                }
            }
        } catch (err) {
            removeTypingIndicator();
            fullText = `❌ Lỗi: ${err.message}`;
            appendMessageToDOM('assistant', fullText);
        }

        // Save assistant message
        session.messages.push({
            role: 'assistant',
            content: fullText,
            timestamp: new Date().toISOString(),
        });
        session.updatedAt = new Date().toISOString();
        saveSessions();

        // Re-enable input
        isGenerating = false;
        messageInput.disabled = false;
        messageInput.placeholder = 'Nhập câu hỏi tuyển sinh...';
        messageInput.focus();
        updateSendButton();
    }

    // =========================================================================
    // SEND IMAGE MESSAGE (OCR + STREAM)
    // =========================================================================
    async function sendImageMessage(question) {
        if (isGenerating) return;

        const imageFile = pendingImageFile;
        const imageDataUrl = imagePreviewImg.src; // for showing in chat
        clearImage();

        isGenerating = true;
        sendBtn.classList.remove('active');
        sendBtn.disabled = true;
        messageInput.disabled = true;
        messageInput.placeholder = '🔍 Đang nhận dạng ảnh (OCR)...';

        const displayQuestion = question.trim() || '📷 Phân tích ảnh tải lên';

        // Ensure we have an active session
        let session;
        if (!activeSessionId) {
            session = createSession();
            activeSessionId = session.id;
            welcomeScreen.classList.add('hidden');
            messagesEl.style.display = 'block';
            messagesEl.innerHTML = '';
        } else {
            session = getSession(activeSessionId);
        }

        if (session.messages.length === 0) {
            session.title = displayQuestion.length > 40
                ? displayQuestion.slice(0, 40) + '...'
                : displayQuestion;
        }

        session.messages.push({
            role: 'user',
            content: displayQuestion,
            timestamp: new Date().toISOString(),
        });
        session.updatedAt = new Date().toISOString();
        saveSessions();

        // Show user message with image thumbnail
        const userMsgEl = appendMessageToDOM('user', displayQuestion);
        const userTextEl = userMsgEl.querySelector('.message-text');
        const imgThumb = document.createElement('img');
        imgThumb.src = imageDataUrl;
        imgThumb.className = 'message-image-preview';
        userTextEl.prepend(imgThumb);

        renderHistory();
        topbarTitle.textContent = session.title;

        // Show typing indicator
        appendTypingIndicator();

        // Build FormData
        const providerSelect = document.getElementById('llmProvider');
        const provider = providerSelect ? providerSelect.value : 'openrouter';

        const formData = new FormData();
        formData.append('image', imageFile);
        formData.append('question', question.trim());
        formData.append('provider', provider);

        let fullText = '';
        const textId = 'stream_img_' + Date.now();

        try {
            const response = await fetch(`${API_BASE}/chat/stream/image`, {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                const err = await response.json().catch(() => ({ detail: 'Lỗi kết nối' }));
                throw new Error(err.detail || `HTTP ${response.status}`);
            }

            removeTypingIndicator();

            // Create assistant message element
            const msgEl = appendMessageToDOM('assistant', '', { textId, isStreaming: true });
            const textEl = msgEl.querySelector(`#${textId}`);

            // Add OCR badge
            const ocrBadge = document.createElement('div');
            ocrBadge.className = 'ocr-badge processing';
            ocrBadge.innerHTML = '<span class="ocr-badge-dot"></span> Đang OCR...';
            textEl.parentElement.insertBefore(ocrBadge, textEl);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const jsonStr = line.slice(6).trim();
                    if (!jsonStr) continue;

                    try {
                        const data = JSON.parse(jsonStr);

                        if (data.error) {
                            fullText += `\n\n❌ Lỗi: ${data.error}`;
                            textEl.innerHTML = renderMarkdown(fullText);
                            scrollToBottom();
                            continue;
                        }

                        // OCR completed event
                        if (data.ocr_done) {
                            ocrBadge.className = 'ocr-badge done';
                            const subjectCount = data.ocr_result?.total_subjects || data.ocr_result?.subjects?.length || 0;
                            ocrBadge.innerHTML = `<span class="ocr-badge-dot"></span> OCR hoàn tất — ${subjectCount} môn nhận dạng`;
                            continue;
                        }

                        if (data.token) {
                            fullText += data.token;
                            textEl.innerHTML = renderMarkdown(fullText) + '<span class="cursor-blink">▌</span>';
                            scrollToBottom();
                        }

                        if (data.done) {
                            if (data.full_answer) {
                                fullText = data.full_answer;
                            } else {
                                const citationBlock = buildCitationBlock(data);
                                if (citationBlock) {
                                    fullText = `${fullText}\n\n${citationBlock}`.trim();
                                }
                            }
                            textEl.innerHTML = renderMarkdown(fullText);
                            scrollToBottom();
                        }
                    } catch {
                        // Skip malformed JSON
                    }
                }
            }
        } catch (err) {
            removeTypingIndicator();
            fullText = `❌ Lỗi: ${err.message}`;
            appendMessageToDOM('assistant', fullText);
        }

        // Save assistant message
        session.messages.push({
            role: 'assistant',
            content: fullText,
            timestamp: new Date().toISOString(),
        });
        session.updatedAt = new Date().toISOString();
        saveSessions();

        // Re-enable input
        isGenerating = false;
        messageInput.disabled = false;
        messageInput.placeholder = 'Nhập câu hỏi tuyển sinh...';
        messageInput.focus();
        updateSendButton();
    }

    // =========================================================================
    // EVENT HANDLERS
    // =========================================================================
    function updateSendButton() {
        if ((messageInput.value.trim() || pendingImageFile) && !isGenerating) {
            sendBtn.classList.add('active');
            sendBtn.disabled = false;
        } else {
            sendBtn.classList.remove('active');
            sendBtn.disabled = true;
        }
    }

    // Auto-resize textarea
    function autoResize() {
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
    }

    // Send on Enter (Shift+Enter for new line)
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const q = messageInput.value.trim();
            if ((q || pendingImageFile) && !isGenerating) {
                messageInput.value = '';
                autoResize();
                sendMessage(q);
            }
        }
    });

    messageInput.addEventListener('input', () => {
        updateSendButton();
        autoResize();
    });

    sendBtn.addEventListener('click', () => {
        const q = messageInput.value.trim();
        if ((q || pendingImageFile) && !isGenerating) {
            messageInput.value = '';
            autoResize();
            sendMessage(q);
        }
    });

    // ---- Image upload events ----
    uploadImgBtn.addEventListener('click', () => {
        if (pendingImageFile) {
            clearImage();
        } else {
            imageFileInput.click();
        }
    });

    imageFileInput.addEventListener('change', () => {
        if (imageFileInput.files && imageFileInput.files[0]) {
            selectImage(imageFileInput.files[0]);
        }
    });

    imagePreviewRemove.addEventListener('click', () => {
        clearImage();
    });

    // Drag & drop on input area
    const inputArea = $('#inputArea');
    inputArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        inputArea.style.borderColor = 'var(--accent)';
    });
    inputArea.addEventListener('dragleave', () => {
        inputArea.style.borderColor = '';
    });
    inputArea.addEventListener('drop', (e) => {
        e.preventDefault();
        inputArea.style.borderColor = '';
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            const file = e.dataTransfer.files[0];
            if (file.type.startsWith('image/')) {
                selectImage(file);
            }
        }
    });

    // Paste image from clipboard
    messageInput.addEventListener('paste', (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) selectImage(file);
                return;
            }
        }
    });

    // Suggested questions
    suggestions.addEventListener('click', (e) => {
        const card = e.target.closest('.suggestion-card');
        if (!card) return;
        const question = card.dataset.question;
        if (question) sendMessage(question);
    });

    // New chat
    newChatBtn.addEventListener('click', () => {
        showWelcome();
        messageInput.value = '';
        clearImage();
        messageInput.focus();
        // Also clear backend session
        fetch(`${API_BASE}/session/clear`, { method: 'POST' }).catch(() => { });
    });

    // Sidebar toggle
    closeSidebarBtn.addEventListener('click', () => sidebar.classList.add('collapsed'));
    openSidebarBtn.addEventListener('click', () => sidebar.classList.remove('collapsed'));

    // Search
    searchInput.addEventListener('input', () => {
        renderHistory(searchInput.value);
    });

    // =========================================================================
    // INIT
    // =========================================================================
    function init() {
        loadSessions();
        renderHistory();
        showWelcome();

        // Check API status
        fetch(`${API_BASE}/status`)
            .then(r => r.json())
            .then(data => {
                const statusEl = document.querySelector('.topbar-status');
                if (data.engine_ready) {
                    statusEl.innerHTML = '<span class="status-dot online"></span><span>Sẵn sàng</span>';
                } else {
                    statusEl.innerHTML = '<span class="status-dot"></span><span>Chưa sẵn sàng</span>';
                }
            })
            .catch(() => {
                const statusEl = document.querySelector('.topbar-status');
                statusEl.innerHTML = '<span class="status-dot"></span><span>Mất kết nối</span>';
            });

        // Responsive: collapse sidebar on mobile
        if (window.innerWidth <= 768) {
            sidebar.classList.add('collapsed');
        }
    }

    init();
})();
