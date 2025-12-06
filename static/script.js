function initSocket(roomId, playerId) {
    const socket = io();
    
    socket.on('connect', () => {
        socket.emit('join', { room_id: roomId, player_id: playerId });
    });
    
    socket.on('update_players', (data) => {
        document.getElementById('players').innerHTML = '<h2>Player List:</h2>' + data.players.map(p => `<p>${p}</p>`).join('');
    });
    
    socket.on('start_game', () => {
        window.location.href = `/game/${roomId}`;
    });
    
    socket.on('room_closed', () => {
        alert('Room closed');
        window.location.href = '/';
    });
    
    document.getElementById('leave').addEventListener('click', () => {
        socket.emit('leave', { room_id: roomId, player_id: playerId });
        window.location.href = '/';
    });
    
    const startBtn = document.getElementById('start');
    if (startBtn) {
        startBtn.addEventListener('click', () => {
            socket.emit('start', { room_id: roomId, player_id: playerId });
        });
    }
}

function initGameSocket(roomId, playerId) {
    const socket = io();
    let countdown;
    
    socket.on('connect', () => {
        socket.emit('join', { room_id: roomId, player_id: playerId });
        socket.emit('get_hand', { room_id: roomId, player_id: playerId });
    });
    
    socket.on('update_game', (state) => {
        let playersHtml = '<h2>Players:</h2>';
        state.player_order.forEach(p => {
            playersHtml += `<p>${p}: ${state.players[p]} (Bet: ${state.bets[p]}) ${state.folded[p] ? '(Folded)' : ''}</p>`;
        });
        document.getElementById('players-list').innerHTML = playersHtml;
        
        let commHtml = '<h2>Community:</h2>' + state.community.map(card => {
            const suit = card.slice(-1);
            const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
            return `<div class="card ${color}">${card.slice(0, -1)}${SUITS[suit]}</div>`;
        }).join('');
        document.getElementById('community').innerHTML = commHtml;
        
        document.getElementById('pot').innerHTML = `Pot: ${state.pot}`;
        
        const isTurn = state.current_player === playerId;
        document.getElementById('turn').innerHTML = isTurn ? 'Your Turn!' : `Waiting for ${state.current_player}`;
        
        let actionsHtml = '';
        if (isTurn) {
            const maxBet = Math.max(...Object.values(state.bets));
            const toCall = maxBet - state.bets[playerId];
            const pot = state.pot;
            const minRaise = state.min_raise;
            const minBet = toCall === 0 ? BIG_BLIND : (toCall + minRaise);
            if (toCall === 0) {
                actionsHtml += '<button onclick="sendAction(\'check\')">Check</button>';
                actionsHtml += '<button onclick="promptBet(\'bet\', ' + minBet + ')">Bet</button>';
            } else {
                actionsHtml += '<button onclick="sendAction(\'call\')">Call</button>';
                actionsHtml += '<button onclick="promptBet(\'raise\', ' + minBet + ')">Raise</button>';
            }
            actionsHtml += '<br>Quick Bets: <button onclick="quickBet(\'bet\', ' + Math.max(pot / 2, minBet) + ', ' + minBet + ')">1/2 Pot</button>';
            actionsHtml += '<button onclick="quickBet(\'bet\', ' + Math.max(pot, minBet) + ', ' + minBet + ')">Pot</button>';
            actionsHtml += '<button onclick="quickBet(\'bet\', ' + Math.max(pot * 2, minBet) + ', ' + minBet + ')">2x Pot</button>';
            actionsHtml += '<input id="custom-bet" type="number" min="' + minBet + '"><button onclick="sendCustomBet(\'bet\', ' + minBet + ')">Custom</button>';
        }
        document.getElementById('actions').innerHTML = actionsHtml;
        
        if (isTurn) {
            let timeLeft = 30;
            document.getElementById('timer').innerHTML = `Time: ${timeLeft}s`;
            countdown = setInterval(() => {
                timeLeft--;
                document.getElementById('timer').innerHTML = `Time: ${timeLeft}s`;
                if (timeLeft <= 0) clearInterval(countdown);
            }, 1000);
        } else {
            clearInterval(countdown);
            document.getElementById('timer').innerHTML = '';
        }
    });
    
    socket.on('showdown', (data) => {
        let winHtml = '<h2>Showdown!</h2>';
        for (let w in data.winners) {
            winHtml += `<p>${w} wins ${data.winners[w]} (Hand: ${data.hands[w].join(', ')})</p>`;
        }
        document.getElementById('players-list').innerHTML += winHtml;
    });
    
    socket.on('game_over', () => {
        alert('Game Over');
        window.location.href = '/';
    });
    
    socket.on('your_hand', (hand) => {
        let handHtml = '<h2>Your Hand:</h2>' + hand.map(card => {
            const suit = card.slice(-1);
            const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
            return `<div class="card ${color}">${card.slice(0, -1)}${SUITS[suit]}</div>`;
        }).join('');
        document.getElementById('your-hand').innerHTML = handHtml;
    });
    
    document.getElementById('fold').addEventListener('click', () => {
        sendAction('fold');
    });
    
    document.getElementById('leave').addEventListener('click', () => {
        socket.emit('leave', { room_id: roomId, player_id: playerId });
        window.location.href = '/';
    });
    
    window.sendAction = function(action) {
        clearInterval(countdown);
        socket.emit('action', { room_id: roomId, player_id: playerId, action: action });
    };
    
    window.promptBet = function(action, min) {
        const amount = prompt('Enter amount (must be positive and >= ' + min + '):');
        const parsed = parseInt(amount);
        if (parsed > 0 && parsed >= min) {
            socket.emit('action', { room_id: roomId, player_id: playerId, action: action, amount: parsed });
        } else {
            alert('Invalid amount. Must be positive and at least ' + min + '. Please try again.');
        }
    };
    
    window.quickBet = function(action, amount, min) {
        amount = Math.max(amount, min);
        socket.emit('action', { room_id: roomId, player_id: playerId, action: action, amount: amount });
    };
    
    window.sendCustomBet = function(action, min) {
        const amount = parseInt(document.getElementById('custom-bet').value);
        if (amount > 0 && amount >= min) {
            socket.emit('action', { room_id: roomId, player_id: playerId, action: action, amount: amount });
        } else {
            alert('Invalid custom amount. Must be positive and at least ' + min + '.');
        }
    };
}

const SUITS = { 's': '♠', 'h': '♥', 'd': '♦', 'c': '♣' };