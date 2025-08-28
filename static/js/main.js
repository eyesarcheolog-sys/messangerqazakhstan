document.addEventListener('DOMContentLoaded', () => {
    // --- CORE JAVASCRIPT ---
    const socket = io();
    const form = document.getElementById('form');
    const input = document.getElementById('input');
    const messages = document.getElementById('messages');
    const allLists = document.querySelectorAll('.chat-list');
    const username = document.body.dataset.username;
    let currentChat = { type: null, id: null, name: null };
    const initialData = document.getElementById('initial-data');
    let unreadCounts = JSON.parse(initialData.dataset.unreadCounts);

    // Improvement: Auto-scroll when mobile keyboard appears
    if(input) {
        input.addEventListener('focus', () => {
            setTimeout(() => {
                if (messages) {
                    messages.scrollTop = messages.scrollHeight;
                }
            }, 100);
        });
    }

    function initializeUnreadCounts() {
        for (const key in unreadCounts) {
            if (unreadCounts[key] > 0) {
                const isGroup = key.startsWith('group_');
                const notifId = isGroup ? `notif-group-${key.split('_')[1]}` : `notif-${key}`;
                const notifIndicator = document.getElementById(notifId);
                if (notifIndicator) {
                    notifIndicator.textContent = unreadCounts[key];
                    notifIndicator.classList.add('visible');
                }
            }
        }
    }
    
    // Вызываем функцию после ее объявления
    initializeUnreadCounts();

    function appendMessage(data) {
        const item = document.createElement('li');
    
        if (data.sender === username) {
            item.classList.add('my-message');
        } else {
            item.classList.add('other-message');
        }

        if (currentChat.type === 'group' && data.sender !== username) {
            const senderName = document.createElement('div');
            senderName.textContent = data.sender;
            senderName.style.fontWeight = 'bold';
            senderName.style.fontSize = '13px';
            senderName.style.color = 'var(--accent-color)';
            senderName.style.marginBottom = '4px';
            item.appendChild(senderName);
        }

        if (data.audio_url) {
            const audioPlayer = document.createElement('audio');
            audioPlayer.controls = true;
            audioPlayer.src = data.audio_url;
            item.appendChild(audioPlayer);

            if (data.transcription && data.transcription.trim() !== "") {
                const transcriptionP = document.createElement('p');
                transcriptionP.className = 'transcription-content';
                transcriptionP.textContent = `"${data.transcription}"`;
                
                const toggleBtn = document.createElement('button');
                toggleBtn.className = 'toggle-transcription-btn';
                toggleBtn.textContent = 'Показать текст';
                toggleBtn.onclick = () => {
                    transcriptionP.classList.toggle('visible');
                    toggleBtn.textContent = transcriptionP.classList.contains('visible') ? 'Скрыть текст' : 'Показать текст';
                };
                item.appendChild(toggleBtn);
                item.appendChild(transcriptionP);
            }
        } 
        else if (data.message) {
            const messageContent = document.createElement('span');
            messageContent.textContent = data.message;
            item.appendChild(messageContent);
        }
        
        const timestampSpan = document.createElement('span');
        timestampSpan.className = 'timestamp';
        const date = new Date(data.timestamp);
        timestampSpan.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        item.appendChild(timestampSpan);

        messages.appendChild(item);
        messages.scrollTop = messages.scrollHeight;
    }

    allLists.forEach(list => {
        list.addEventListener('click', function(e) {
            const li = e.target.closest('li');
            if (li) {
                document.querySelectorAll('.chat-list li').forEach(item => item.classList.remove('active'));
                li.classList.add('active');
                currentChat.type = li.dataset.type;
                currentChat.id = li.dataset.id;
                currentChat.name = li.dataset.name;
                const countKey = currentChat.type === 'user' ? currentChat.name : `group_${currentChat.id}`;
                unreadCounts[countKey] = 0;
                const notifId = currentChat.type === 'user' ? `notif-${currentChat.name}` : `notif-group-${currentChat.id}`;
                if (document.getElementById(notifId)) {
                    document.getElementById(notifId).classList.remove('visible');
                }
                messages.innerHTML = '';
                const headerLink = document.getElementById('chat-header-link');
                const chatHeader = document.getElementById('chat-header');
                
                if(currentChat.type === 'group') {
                    headerLink.href = `/group/${currentChat.id}`;
                    // В мобильной версии нам нужен другой текст
                    if (window.innerWidth <= 768) {
                        chatHeader.querySelector('h2').textContent = currentChat.name;
                    } else {
                        headerLink.textContent = currentChat.name;
                    }
                } else {
                    headerLink.href = '#';
                    const chatTitle = `Чат с ${currentChat.name}`;
                    if (window.innerWidth <= 768) {
                        chatHeader.querySelector('h2').textContent = chatTitle;
                    } else {
                       headerLink.textContent = chatTitle;
                    }
                }

                const historyUrl = currentChat.type === 'user' ? `/history/${currentChat.name}` : `/history/group/${currentChat.id}`;
                fetch(historyUrl).then(response => response.json()).then(history => history.forEach(appendMessage));

                // Показываем чат на мобильных
                if (window.innerWidth <= 768) {
                    document.body.classList.add('mobile-chat-view');
                }
            }
        });
    });

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        if (input.value && currentChat.type) {
            if (currentChat.type === 'user') {
                socket.emit('private_message', { recipient: currentChat.name, message: input.value });
            } else if (currentChat.type === 'group') {
                socket.emit('group_message', { group_id: currentChat.id, message: input.value });
            }
            input.value = '';
        }
    });

    socket.on('receive_private_message', function(data) {
        if (currentChat.type === 'user' && (data.sender === currentChat.name || data.sender === username)) {
            appendMessage(data);
        }
    });
    socket.on('receive_group_message', function(data) {
        if (currentChat.type === 'group' && data.group_id == currentChat.id) {
            appendMessage(data);
        }
    });
    socket.on('receive_voice_message', function(data) {
        const isGroupChat = data.hasOwnProperty('group_id');
        if ((isGroupChat && currentChat.type === 'group' && data.group_id == currentChat.id) || 
           (!isGroupChat && currentChat.type === 'user' && (data.sender === currentChat.name || data.sender === username))) {
            appendMessage(data);
        }
    });
    socket.on('update_online_users', function(online_users) {
        document.querySelectorAll('.user-avatar').forEach(avatar => {
            const indicator = avatar.querySelector('.online-indicator');
            const userName = avatar.parentElement.dataset.name;
            if (indicator && userName) {
                indicator.classList.toggle('online', online_users.includes(userName));
            }
        });
    });
    socket.on('new_message_notification', function(data) {
        if (data.sender && (currentChat.type !== 'user' || data.sender !== currentChat.name)) {
            const countKey = data.sender;
            unreadCounts[countKey] = (unreadCounts[countKey] || 0) + 1;
            const notifIndicator = document.getElementById(`notif-${countKey}`);
            if (notifIndicator) {
                notifIndicator.textContent = unreadCounts[countKey];
                notifIndicator.classList.add('visible');
            }
        } else if (data.group_id && (currentChat.type !== 'group' || data.group_id != currentChat.id)) {
            const countKey = `group_${data.group_id}`;
            unreadCounts[countKey] = (unreadCounts[countKey] || 0) + 1;
            const notifIndicator = document.getElementById(`notif-group-${data.group_id}`);
            if (notifIndicator) {
                notifIndicator.textContent = unreadCounts[countKey];
                notifIndicator.classList.add('visible');
            }
        }
    });

    // --- MODAL AND MOBILE LOGIC ---
    const modal = document.getElementById('createGroupModal');
    if (modal) {
        const createBtn = document.getElementById('create-group-btn');
        const closeBtn = modal.querySelector('.close-btn');
        if(createBtn) createBtn.onclick = () => modal.style.display = "block";
        if(closeBtn) closeBtn.onclick = () => modal.style.display = "none";
        window.onclick = (event) => { if (event.target == modal) modal.style.display = "none"; };
    }
    
    const backToChatsBtn = document.getElementById('chat-header-back-btn');
    if (backToChatsBtn) {
        backToChatsBtn.addEventListener('click', () => {
            document.body.classList.remove('mobile-chat-view');
            currentChat = { type: null, id: null, name: null };
            document.querySelectorAll('.chat-list li').forEach(item => item.classList.remove('active'));
            const chatHeader = document.getElementById('chat-header');
            if (chatHeader) {
                chatHeader.querySelector('h2').textContent = "Выберите чат";
            }
        });
    }

    // --- VOICE MESSAGE MODAL LOGIC ---
    const mainRecordBtn = document.getElementById('record-btn');
    const voiceModal = document.getElementById('voice-modal');
    if(voiceModal){
        const startBtn = document.getElementById('start-record-btn');
        const stopBtn = document.getElementById('stop-record-btn');
        const improveAiBtn = document.getElementById('improve-ai-btn');
        const generateAiBtn = document.getElementById('generate-ai-btn');
        const aiModelSelect = document.getElementById('ai-model-select');
        const deleteBtn = document.getElementById('delete-record-btn');
        const sendAudioBtn = document.getElementById('send-audio-btn');
        const sendTextBtn = document.getElementById('send-text-btn');
        const statusDisplay = document.getElementById('voice-status');
        const playbackContainer = document.getElementById('audio-playback-container');
        const audioPlayer = document.getElementById('audio-playback');
        const transcriptionContainer = document.getElementById('transcription-container');
        const transcriptionText = document.getElementById('transcription-text');

        let mediaRecorder;
        let audioChunks = [];
        let recordedBlob = null;
        let recognition;
        let recordingInterval;

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            recognition = new SpeechRecognition();
            recognition.lang = 'ru-RU';
            recognition.continuous = true;
            recognition.interimResults = true;
        }

        function updateUI(state) {
            if(!startBtn) return; // Проверка на существование элементов
            startBtn.style.display = (state === 'idle') ? 'block' : 'none';
            stopBtn.style.display = (state === 'recording') ? 'block' : 'none';
            playbackContainer.style.display = (state === 'recorded' || state === 'transcribed') ? 'block' : 'none';
            transcriptionContainer.style.display = (state === 'recorded' || state === 'transcribed') ? 'block' : 'none';
            
            const showAiButtons = (state === 'transcribed' && transcriptionText.value);
            aiModelSelect.style.display = showAiButtons ? 'inline-block' : 'none';
            improveAiBtn.style.display = showAiButtons ? 'block' : 'none';
            generateAiBtn.style.display = showAiButtons ? 'block' : 'none';
            
            sendAudioBtn.style.display = (state === 'recorded' || state === 'transcribed') ? 'block' : 'none';
            sendTextBtn.style.display = (state === 'transcribed' && transcriptionText.value) ? 'block' : 'none';

            if(state === 'idle') statusDisplay.textContent = "Нажмите 'Старт' для начала записи";
            if(state === 'recording') statusDisplay.textContent = `Идет запись: 0 сек.`;
            if(state === 'recorded') statusDisplay.textContent = "Запись завершена";
            if(state === 'transcribed') statusDisplay.textContent = "Транскрипция готова";
        }

        function resetModal() {
            recordedBlob = null;
            audioChunks = [];
            if(transcriptionText) transcriptionText.value = "";
            if(audioPlayer) audioPlayer.src = "";
            if (recordingInterval) clearInterval(recordingInterval);
            updateUI('idle');
        }

        if(mainRecordBtn) mainRecordBtn.onclick = () => {
            if (!currentChat.type) { alert("Пожалуйста, выберите чат."); return; }
            voiceModal.style.display = 'flex';
            resetModal();
        };

        if(deleteBtn) deleteBtn.onclick = () => {
            if (mediaRecorder && mediaRecorder.stream) {
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
            }
            if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
            if (recognition) {
                try { recognition.stop(); } catch(e) { /* ignore */ }
            }
            voiceModal.style.display = 'none';
        };

        if(startBtn) startBtn.onclick = async () => {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                updateUI('recording');

                let seconds = 0;
                recordingInterval = setInterval(() => {
                    seconds++;
                    statusDisplay.textContent = `Идет запись: ${seconds} сек.`;
                }, 1000);

                if (recognition) {
                    recognition.onresult = (event) => {
                        let final_transcript = '';
                        for (let i = event.resultIndex; i < event.results.length; ++i) {
                            final_transcript += event.results[i][0].transcript;
                        }
                        transcriptionText.value = final_transcript;
                    };
                    recognition.onend = () => updateUI('transcribed');
                    recognition.start();
                }

                mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
                mediaRecorder.onstop = () => {
                    clearInterval(recordingInterval);
                    recordedBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    audioPlayer.src = URL.createObjectURL(recordedBlob);
                    if (!recognition) updateUI('recorded');
                    stream.getTracks().forEach(track => track.stop());
                };
                
                mediaRecorder.start();
            } catch (err) {
                console.error("Ошибка микрофона:", err);
                statusDisplay.textContent = "Ошибка доступа к микрофону.";
            }
        };

        if(stopBtn) stopBtn.onclick = () => {
            if (mediaRecorder) mediaRecorder.stop();
            if (recognition) {
                try { recognition.stop(); } catch(e) { /* ignore */ }
            }
        };
        
        async function callAI(taskType) {
            const text = transcriptionText.value;
            const selectedModel = aiModelSelect.value;
            if (!text) return;
            
            const originalStatus = statusDisplay.textContent;
            statusDisplay.textContent = "ИИ работает...";
            improveAiBtn.disabled = true;
            generateAiBtn.disabled = true;
            aiModelSelect.disabled = true;

            try {
                const response = await fetch('/edit_with_ai', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text, model: selectedModel, task_type: taskType })
                });
                const data = await response.json();
                if (data.edited_text) {
                    transcriptionText.value = data.edited_text;
                } else if (data.error) {
                    console.error("AI Error:", data.error);
                    alert("Произошла ошибка при обращении к ИИ.");
                }
            } catch (err) {
                console.error("Ошибка ИИ:", err);
            }
            
            statusDisplay.textContent = originalStatus;
            improveAiBtn.disabled = false;
            generateAiBtn.disabled = false;
            aiModelSelect.disabled = false;
        }
        
        if(improveAiBtn) improveAiBtn.onclick = () => callAI('improve');
        if(generateAiBtn) generateAiBtn.onclick = () => callAI('generate');

        if(sendAudioBtn) sendAudioBtn.onclick = () => {
            if(!recordedBlob) return;
            const formData = new FormData();
            formData.append('audio', recordedBlob, 'recording.webm');
            formData.append('transcription', transcriptionText.value.trim());
            
            if (currentChat.type === 'user') formData.append('recipient', currentChat.name);
            else if (currentChat.type === 'group') formData.append('group_id', currentChat.id);
            
            fetch('/send_audio', { method: 'POST', body: formData });
            voiceModal.style.display = 'none';
        };

        if(sendTextBtn) sendTextBtn.onclick = () => {
            const messageText = transcriptionText.value;
            if (!messageText) return;
            if (currentChat.type === 'user') {
                socket.emit('private_message', { recipient: currentChat.name, message: messageText });
            } else if (currentChat.type === 'group') {
                socket.emit('group_message', { group_id: currentChat.id, message: messageText });
            }
            voiceModal.style.display = 'none';
        };
    }


    // --- AI ASSISTANT MODAL LOGIC ---
    const assistantModal = document.getElementById('assistant-modal');
    if(assistantModal) {
        const openAssistantBtn = document.getElementById('assistant-btn');
        // Предполагаем, что у кнопки закрытия есть id="close-assistant-btn"
        const closeAssistantBtn = assistantModal.querySelector('.close-btn') || document.getElementById('close-assistant-btn'); 
        const assistantForm = document.getElementById('assistant-form');
        const assistantInput = document.getElementById('assistant-input');
        const assistantMessages = document.getElementById('assistant-messages');
        const sendToChatBtn = document.getElementById('send-to-chat-btn');
        
        if(openAssistantBtn) openAssistantBtn.onclick = () => {
            assistantModal.style.display = 'flex';
        };

        if(closeAssistantBtn) closeAssistantBtn.onclick = () => {
            assistantModal.style.display = 'none';
        };
        
        // Закрытие по клику вне модального окна
        window.addEventListener('click', (event) => {
            if (event.target == assistantModal) {
                assistantModal.style.display = "none";
            }
        });


        if(assistantForm) assistantForm.onsubmit = async (e) => {
            e.preventDefault();
            const userPrompt = assistantInput.value;
            if (!userPrompt) return;

            addAssistantMessage(userPrompt, 'user');
            assistantInput.value = '';
            
            const thinkingBubble = addAssistantMessage('Думаю...', 'assistant');

            try {
                const response = await fetch('/chat_with_assistant', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ prompt: userPrompt })
                });

                if (!response.ok) {
                    throw new Error(`Server responded with status: ${response.status}`);
                }

                const data = await response.json();
                thinkingBubble.textContent = data.response || "Не удалось получить ответ от ИИ.";

            } catch (error) {
                console.error("Error with AI Assistant:", error);
                thinkingBubble.textContent = "Произошла ошибка сети. Попробуйте снова.";
            }
            assistantMessages.scrollTop = assistantMessages.scrollHeight;
        };

        if(sendToChatBtn) sendToChatBtn.onclick = () => {
            const lastAssistantResponse = assistantMessages.querySelector('.assistant-bubble.assistant:last-child');
            if (lastAssistantResponse && lastAssistantResponse.textContent !== "Думаю...") {
                input.value = lastAssistantResponse.textContent;
                assistantModal.style.display = 'none';
            }
        };

        function addAssistantMessage(text, role) {
            const bubble = document.createElement('div');
            bubble.className = `assistant-bubble ${role}`;
            bubble.textContent = text;
            assistantMessages.appendChild(bubble);
            assistantMessages.scrollTop = assistantMessages.scrollHeight;
            return bubble;
        }
    }
});