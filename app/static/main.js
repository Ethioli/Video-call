// --- UI Elements ---
const userIdSpan = document.getElementById('user-id');
const remoteIdInput = document.getElementById('remote-id-input');
const callButton = document.getElementById('call-btn');
const localVideo = document.getElementById('local-video');
const remoteVideo = document.getElementById('remote-video');
const messageBox = document.getElementById('message-box');

// --- Global State ---
let localStream;
let peerConnection;
const STUN_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];
const localUserId = Math.floor(Math.random() * 100000);
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

async function init() {
    userIdSpan.textContent = localUserId;
    // Get local media stream (camera and microphone)
    try {
        localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        localVideo.srcObject = localStream;
    } catch (err) {
        console.error("Error accessing media devices: ", err);
        showMessage("Could not access your camera and microphone. Please check permissions.");
        return;
    }

    // Connect to WebSocket server for signaling
    // Use the global variable provided by the server
    websocket = new WebSocket(`${WS_URL}/${localUserId}`);

    websocket.onopen = () => {
        console.log('WebSocket connection established.');
        showMessage('Connected to server. Ready to call!', 'success');
    };

    websocket.onmessage = async (event) => {
        const message = JSON.parse(event.data);
        console.log("Received message:", message);
        
        // Handle different signaling messages
        if (message.type === 'offer') {
            // Received an offer from a remote peer, must respond with an answer
            remoteUserId = message.sender_id;
            remoteIdInput.value = remoteUserId;
            await handleOffer(message.payload);
        } else if (message.type === 'answer' && peerConnection) {
            // Received an answer to our offer, set it as the remote description
            await peerConnection.setRemoteDescription(new RTCSessionDescription(message.payload));
        } else if (message.type === 'ice-candidate' && peerConnection) {
            // Received a new ICE candidate, add it to the peer connection
            try {
                await peerConnection.addIceCandidate(new RTCIceCandidate(message.payload));
            } catch (e) {
                console.error('Error adding received ice candidate', e);
            }
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
async function createPeerConnection() {
    peerConnection = new RTCPeerConnection({ iceServers: STUN_SERVERS });
    
    // Add local video/audio tracks to the connection
    localStream.getTracks().forEach(track => {
        peerConnection.addTrack(track, localStream);
    });

    // Listen for remote tracks and attach them to the remote video element
    peerConnection.ontrack = (event) => {
        console.log("Remote track received:", event.track);
        remoteVideo.srcObject = event.streams[0];
    };

    // Listen for new ICE candidates and send them to the remote peer via the signaling server
    peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
            console.log("Sending ICE candidate:", event.candidate);
            websocket.send(JSON.stringify({
                type: 'ice-candidate',
                target_id: remoteUserId,
                payload: event.candidate
            }));
        }
    };
}

async function createOffer() {
    remoteUserId = remoteIdInput.value;
    if (!remoteUserId) {
        showMessage("Please enter a remote user ID.");
        return;
    }
    if (remoteUserId === localUserId.toString()) {
        showMessage("You can't call yourself!");
        return;
    }

    await createPeerConnection();
    
    // Create an offer and set it as the local description
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);

    // Send the offer to the remote user via the signaling server
    websocket.send(JSON.stringify({
        type: 'offer',
        target_id: remoteUserId,
        payload: peerConnection.localDescription
    }));

    showMessage(`Calling user ${remoteUserId}...`, 'success');
}

async function handleOffer(offerPayload) {
    await createPeerConnection();
    
    // Set the received offer as the remote description
    await peerConnection.setRemoteDescription(new RTCSessionDescription(offerPayload));
    
    // Create an answer and set it as the local description
    const answer = await peerConnection.createAnswer();
    await peerConnection.setLocalDescription(answer);

    // Send the answer back to the original caller
    websocket.send(JSON.stringify({
        type: 'answer',
        target_id: remoteUserId,
        payload: peerConnection.localDescription
    }));

    showMessage(`Incoming call from ${remoteUserId}. Answering...`, 'success');
}

// --- Event Listeners ---
callButton.addEventListener('click', createOffer);

// Start the application
window.onload = init;
