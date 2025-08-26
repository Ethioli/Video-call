const fullNameDisplay = document.getElementById('full-name-display');
const logoutButton = document.getElementById('logout-btn');
const searchInput = document.getElementById('search-input');
const searchButton = document.getElementById('search-btn');
const friendsList = document.getElementById('friends-list');
const searchResultsList = document.getElementById('search-results');
const answerButton = document.getElementById('answer-btn');
const declineButton = document.getElementById('decline-btn');
const endCallButton = document.getElementById('end-call-btn');
const callButtonsContainer = document.getElementById('call-buttons');
const localVideo = document.getElementById('local-video');
const remoteVideo = document.getElementById('remote-video');
const messageBox = document.getElementById('message-box');

// --- Global State ---
let localStream;
let peerConnection;
const STUN_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];
let localUserId = null;
let remoteUserId = null;
let websocket;

// --- WebSocket & Signaling Logic ---
function showMessage(message, type = 'danger') {
    messageBox.textContent = message;
    messageBox.style.backgroundColor = type === 'danger' ? '#dc3545' : '#198754';
    messageBox.style.display = 'block';
    setTimeout(() => {
        messageBox.style.display = 'none';
    }, 5000);
}

// Function to update the list of online friends in the UI
function updateFriendsList(friends) {
    friendsList.innerHTML = '';
    friends.forEach(friend => {
        const li = document.createElement('li');
        li.className = 'list-group-item d-flex align-items-center justify-content-between';

        const friendInfo = document.createElement('div');
        friendInfo.className = 'd-flex align-items-center';

        const profilePic = document.createElement('img');
        profilePic.src = friend.profile_pic || 'https://placehold.co/40x40/000000/FFFFFF?text=P';
        profilePic.className = 'profile-pic';
        
        const friendDetails = document.createElement('div');
        friendDetails.innerHTML = `
            <div>${friend.full_name}</div>
            <small class="text-muted">${friend.is_online ? 'online' : 'offline'}</small>
        `;

        friendInfo.appendChild(profilePic);
        friendInfo.appendChild(friendDetails);
        li.appendChild(friendInfo);

        const callBtn = document.createElement('button');
        callBtn.className = 'btn btn-primary btn-sm';
        callBtn.textContent = 'Call';
        callBtn.disabled = !friend.is_online;
        callBtn.onclick = (e) => {
            e.stopPropagation();
            remoteUserId = friend.id;
            createOffer();
        };
        li.appendChild(callBtn);
        friendsList.appendChild(li);
    });
}

// Function to update the search results in the UI
function updateSearchResults(users) {
    searchResultsList.innerHTML = '';
    users.forEach(user => {
        if (user.id !== localUserId) {
            const li = document.createElement('li');
            li.className = 'list-group-item search-result-item';

            const userInfo = document.createElement('div');
            userInfo.className = 'd-flex align-items-center';
            const profilePic = document.createElement('img');
            profilePic.src = user.profile_pic || 'https://placehold.co/40x40/000000/FFFFFF?text=P';
            profilePic.className = 'profile-pic';

            const nameAndUsername = document.createElement('div');
            nameAndUsername.innerHTML = `
                <div>${user.full_name}</div>
                <small class="text-muted">@${user.username}</small>
            `;
            userInfo.appendChild(profilePic);
            userInfo.appendChild(nameAndUsername);
            li.appendChild(userInfo);

            const addFriendBtn = document.createElement('button');
            addFriendBtn.className = 'btn btn-success btn-sm';
            addFriendBtn.textContent = 'Add Friend';
            addFriendBtn.onclick = async (e) => {
                e.stopPropagation();
                const formData = new FormData();
                formData.append('friend_username', user.username);
                const response = await fetch('/add_friend', {
                    method: 'POST',
                    body: formData,
                });
                if (response.ok) {
                    showMessage(`Added ${user.full_name} as a friend!`, 'success');
                    fetchFriends(); // Refresh friends list
                } else {
                    const error = await response.json();
                    showMessage(error.detail);
                }
            };
            li.appendChild(addFriendBtn);
            searchResultsList.appendChild(li);
        }
    });
}

async function fetchFriends() {
    try {
        const response = await fetch('/friends');
        if (!response.ok) {
            if (response.status === 401) {
                // Not authenticated, redirect to login page
                window.location.href = '/login_page';
                return;
            }
            throw new Error('Failed to fetch friends');
        }
        const friends = await response.json();
        updateFriendsList(friends);
    } catch (err) {
        console.error("Error fetching friends:", err);
    }
}

async function init() {
    // 1. Get local media stream (camera and microphone)
    try {
        localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        localVideo.srcObject = localStream;
    } catch (err) {
        console.error("Error accessing media devices: ", err);
        showMessage("Could not access your camera and microphone. Please check permissions.");
        return;
    }

    // 2. Extract user ID and username from the main page
    // These values are now provided by the Jinja2 template
    localUserId = USER_ID;
    
    // 3. Connect to WebSocket server using the permanent ID
    // The token is now managed by the browser's cookies
    websocket = new WebSocket(WS_URL);

    websocket.onopen = () => {
        console.log('WebSocket connection established.');
        showMessage('Connected to server. Ready to call!', 'success');
        fetchFriends(); // Initial fetch
    };

    websocket.onmessage = async (event) => {
        const message = JSON.parse(event.data);
        console.log("Received message:", message);
        
        // Handle different signaling messages
        if (message.type === 'offer') {
            remoteUserId = message.sender_id;
            callButtonsContainer.style.display = 'flex';
            answerButton.style.display = 'inline-block';
            declineButton.style.display = 'inline-block';
            showMessage(`Incoming call from ${message.full_name || remoteUserId}.`);
            
            answerButton.onclick = async () => {
                await handleOffer(message.payload);
                callButtonsContainer.style.display = 'flex';
                answerButton.style.display = 'none';
                declineButton.style.display = 'none';
                endCallButton.style.display = 'inline-block';
            };
            
            declineButton.onclick = () => {
                declineCall();
            };
            
        } else if (message.type === 'answer' && peerConnection) {
            await peerConnection.setRemoteDescription(new RTCSessionDescription(message.payload));
            callButtonsContainer.style.display = 'flex';
            endCallButton.style.display = 'inline-block';
            
        } else if (message.type === 'ice-candidate' && peerConnection) {
            try {
                await peerConnection.addIceCandidate(new RTCIceCandidate(message.payload));
            } catch (e) {
                console.error('Error adding received ice candidate', e);
            }
        } else if (message.type === 'decline-call') {
            showMessage(`Call from ${message.full_name || remoteUserId} declined.`);
            endCall();
        } else if (message.type === 'end-call') {
            showMessage(`Call from ${message.full_name || remoteUserId} ended.`);
            endCall();
        } else if (message.type === 'online-friends-update') {
            updateFriendsList(message.payload);
        } else if (message.type === 'error') {
            showMessage(message.message);
        }
    };

    websocket.onclose = () => {
        console.log('WebSocket connection closed.');
        showMessage("Connection to server lost. Please refresh.");
    };

    websocket.onerror = (err) => {
        console.error('WebSocket error: ', err);
        showMessage("WebSocket connection error.");
    };
}

// --- WebRTC Logic ---
function endCall() {
    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }
    remoteVideo.srcObject = null;
    remoteUserId = null;
    
    // Reset buttons
    callButtonsContainer.style.display = 'none';
}

function declineCall() {
    if (remoteUserId && websocket.readyState === WebSocket.OPEN) {
        websocket.send(JSON.stringify({
            type: 'decline-call',
            target_id: remoteUserId
        }));
    }
    endCall();
}

async function createPeerConnection() {
    peerConnection = new RTCPeerConnection({ iceServers: STUN_SERVERS });
    
    localStream.getTracks().forEach(track => {
        peerConnection.addTrack(track, localStream);
    });

    peerConnection.ontrack = (event) => {
        remoteVideo.srcObject = event.streams[0];
    };

    peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
            websocket.send(JSON.stringify({
                type: 'ice-candidate',
                target_id: remoteUserId,
                payload: event.candidate
            }));
        }
    };
}

async function createOffer() {
    if (!remoteUserId) {
        showMessage("Please select or enter a friend to call.");
        return;
    }

    await createPeerConnection();
    
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);

    websocket.send(JSON.stringify({
        type: 'offer',
        target_id: remoteUserId,
        payload: peerConnection.localDescription
    }));

    showMessage(`Calling friend...`, 'success');
    
    callButtonsContainer.style.display = 'flex';
    endCallButton.style.display = 'inline-block';
}

async function handleOffer(offerPayload) {
    await createPeerConnection();
    
    await peerConnection.setRemoteDescription(new RTCSessionDescription(offerPayload));
    
    const answer = await peerConnection.createAnswer();
    await peerConnection.setLocalDescription(answer);

    websocket.send(JSON.stringify({
        type: 'answer',
        target_id: remoteUserId,
        payload: peerConnection.localDescription
    }));

    showMessage(`Incoming call. Answering...`, 'success');
}

// --- Event Listeners ---
logoutButton.addEventListener('click', async () => {
    // The form submission now handles the logout
});

searchButton.addEventListener('click', async () => {
    const query = searchInput.value;
    if (!query) return;
    const response = await fetch(`/search_users?query=${query}`);
    const data = await response.json();
    updateSearchResults(data.users);
});

endCallButton.addEventListener('click', () => {
    if (remoteUserId && websocket.readyState === WebSocket.OPEN) {
        websocket.send(JSON.stringify({
            type: 'end-call',
            target_id: remoteUserId
        }));
    }
    endCall();
});

// Start the application
init();
