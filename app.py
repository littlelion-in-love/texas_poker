import eventlet
eventlet.monkey_patch()
from forms import LoginForm
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import time
import threading
from enum import Enum
from collections import defaultdict
import os

with app.test_request_context()
    form = LoginForm()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

rooms = {}
BIG_BLIND = 100
SMALL_BLIND = 50
SUITS = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
COLORS = {'s': 'black', 'h': 'red', 'd': 'red', 'c': 'black'}
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
MIN_STACK = 1200

class Street(Enum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3
    SHOWDOWN = 4

def create_deck():
    return [rank + suit for suit in 'shdc' for rank in RANKS]

def rank_value(rank):
    return RANKS.index(rank) + 2

def evaluate_hand(hand):  # Full hand evaluation (5 cards)
    ranks = sorted([rank_value(card[:-1]) for card in hand], reverse=True)
    suits = [card[-1] for card in hand]
    flush = len(set(suits)) == 1
    straight = len(set(ranks)) == 5 and max(ranks) - min(ranks) == 4 or set(ranks) == {14, 5, 4, 3, 2}  # Wheel straight

    if flush and straight and ranks == [14, 13, 12, 11, 10]:
        return (10, ranks)  # Royal Flush
    if flush and straight:
        return (9, ranks)   # Straight Flush
    count = defaultdict(int)
    for r in ranks:
        count[r] += 1
    quads = [r for r, c in count.items() if c == 4]
    if quads:
        return (8, [quads[0]] + sorted([r for r in ranks if r != quads[0]], reverse=True))  # Quads
    full_house = sorted([r for r, c in count.items() if c == 3], reverse=True) + sorted([r for r, c in count.items() if c == 2], reverse=True)
    if len(full_house) >= 2:  # 可能有多个，但取最高
        return (7, full_house[:2])  # Full House
    if flush:
        return (6, sorted(ranks, reverse=True))  # Flush
    if straight:
        return (5, ranks)  # Straight
    trips = [r for r, c in count.items() if c == 3]
    if trips:
        return (4, [trips[0]] + sorted([r for r in ranks if r != trips[0]], reverse=True)[:2])  # Trips
    pairs = sorted([r for r, c in count.items() if c == 2], reverse=True)
    if len(pairs) >= 2:
        return (3, pairs[:2] + sorted([r for r in ranks if r not in pairs], reverse=True)[:1])  # Two Pair
    if len(pairs) == 1:
        return (2, [pairs[0]] + sorted([r for r in ranks if r != pairs[0]], reverse=True)[:3])  # Pair
    return (1, ranks)  # High Card

def best_hand(player_hand, community):
    all_cards = player_hand + community
    best = (0, [])
    from itertools import combinations
    for combo in combinations(all_cards, 5):
        score = evaluate_hand(combo)
        if score > best:
            best = score
    return best

def determine_winners(room):
    active_players = [p for p in room['player_order'] if not room['folded'][p] and room['players'][p] >= 0]
    if len(active_players) == 1:
        return {active_players[0]: room['pot']}
    
    hands = {}
    for p in active_players:
        score = best_hand(room['hands'][p], room['community'])
        hands[p] = score
    
    # Sort by hand strength descending
    sorted_players = sorted(active_players, key=lambda p: hands[p], reverse=True)
    
    # Handle side pots
    all_in_stacks = sorted(set(room['bets'][p] for p in room['player_order'] if p in room['bets']))
    pots = {}
    prev = 0
    for cap in all_in_stacks:
        current_pot = 0
        contributors = [p for p in room['player_order'] if room['bets'].get(p, 0) >= cap]
        for p in room['player_order']:
            contrib = min(room['bets'].get(p, 0), cap) - prev
            current_pot += contrib
        # Award to best hand among contributors
        contrib_hands = {p: hands[p] for p in contributors if p in active_players}
        if contrib_hands:
            max_score = max(contrib_hands.values())
            winners = [p for p, s in contrib_hands.items() if s == max_score]
            share = current_pot // len(winners)
            for w in winners:
                pots[w] = pots.get(w, 0) + share
        prev = cap
    
    return pots

def generate_room_id():
    while True:
        room_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        if room_id not in rooms:
            return room_id

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['GET', 'POST'])
def create_room():
    if request.method == 'POST':
        multiple = request.form.get('multiple', type=int)
        if multiple not in [20, 40, 60]:
            multiple = request.form.get('custom_multiple', type=int) or 20
        initial_stack = multiple * BIG_BLIND
        if multiple <= 0 or initial_stack < MIN_STACK:
            return render_template('create_room.html', error=f'Initial stack must be positive and at least {MIN_STACK}.')
        player_id = request.form['player_id']
        
        room_id = generate_room_id()
        rooms[room_id] = {
            'creator': player_id,
            'players': {player_id: initial_stack},
            'player_order': [player_id],
            'stack_multiple': multiple,
            'started': False
        }
        return redirect(url_for('room', room_id=room_id))
    
    return render_template('create_room.html')

@app.route('/join', methods=['GET', 'POST'])
def join_room():
    active_rooms = [rid for rid, r in rooms.items() if not r['started'] and len(r['players']) < 6]
    
    if request.method == 'POST':
        room_id = request.form['room_id']
        player_id = request.form['player_id']
        
        if room_id in rooms and not rooms[room_id]['started'] and len(rooms[room_id]['players']) < 6:
            if player_id not in rooms[room_id]['players']:
                initial_stack = rooms[room_id]['stack_multiple'] * BIG_BLIND
                rooms[room_id]['players'][player_id] = initial_stack
                rooms[room_id]['player_order'].append(player_id)
                return redirect(url_for('room', room_id=room_id))
            else:
                return render_template('join_room.html', error='ID already exists', rooms=active_rooms)
        else:
            return render_template('join_room.html', error='Invalid room or full/started', rooms=active_rooms)
    
    return render_template('join_room.html', rooms=active_rooms)

@app.route('/room/<room_id>')
def room(room_id):
    if room_id not in rooms:
        return redirect(url_for('index'))
    
    room_data = rooms[room_id]
    if room_data['started']:
        return redirect(url_for('game', room_id=room_id))
    
    return render_template('room.html', room_id=room_id, creator=room_data['creator'], players=room_data['player_order'])

@app.route('/game/<room_id>')
def game(room_id):
    if room_id not in rooms or not rooms[room_id]['started']:
        return redirect(url_for('index'))
    
    return render_template('game.html', room_id=room_id)

def start_hand(room_id):
    room = rooms[room_id]
    deck = create_deck()
    random.shuffle(deck)
    room['deck'] = deck
    room['hands'] = {p: [deck.pop(), deck.pop()] for p in room['player_order']}
    room['community'] = []
    room['pot'] = 0
    room['bets'] = {p: 0 for p in room['player_order']}
    room['folded'] = {p: False for p in room['player_order']}
    room['street'] = Street.PREFLOP
    room['dealer_index'] = (room.get('dealer_index', -1) + 1) % len(room['player_order'])
    sb_index = (room['dealer_index'] + 1) % len(room['player_order'])
    bb_index = (room['dealer_index'] + 2) % len(room['player_order'])
    room['current_player'] = (room['dealer_index'] + 3) % len(room['player_order'])  # UTG starts preflop
    room['last_raise_player'] = bb_index
    room['min_raise'] = BIG_BLIND
    
    # Post blinds
    sb_player = room['player_order'][sb_index]
    bb_player = room['player_order'][bb_index]
    sb_bet = min(SMALL_BLIND, room['players'][sb_player])
    bb_bet = min(BIG_BLIND, room['players'][bb_player])
    room['bets'][sb_player] = sb_bet
    room['players'][sb_player] -= sb_bet
    room['bets'][bb_player] = bb_bet
    room['players'][bb_player] -= bb_bet
    room['pot'] += sb_bet + bb_bet
    
    emit('update_game', get_game_state(room_id), to=room_id)
    start_timer(room_id)

def next_street(room_id):
    room = rooms[room_id]
    room['pot'] += sum(room['bets'].values())
    room['bets'] = {p: 0 for p in room['player_order']}
    if room['street'] == Street.PREFLOP:
        room['community'].extend([room['deck'].pop() for _ in range(3)])
        room['street'] = Street.FLOP
    elif room['street'] == Street.FLOP:
        room['community'].append(room['deck'].pop())
        room['street'] = Street.TURN
    elif room['street'] == Street.TURN:
        room['community'].append(room['deck'].pop())
        room['street'] = Street.RIVER
    elif room['street'] == Street.RIVER:
        room['street'] = Street.SHOWDOWN
        showdown(room_id)
        return
    
    room['current_player'] = (room['dealer_index'] + 1) % len(room['player_order'])  # SB starts post-flop
    room['last_raise_player'] = None
    room['min_raise'] = BIG_BLIND
    emit('update_game', get_game_state(room_id), to=room_id)
    if active_count(room_id) > 1:
        start_timer(room_id)
    else:
        showdown(room_id)

def active_count(room_id):
    room = rooms[room_id]
    return sum(1 for p in room['player_order'] if not room['folded'][p] and room['players'][p] > 0)

def showdown(room_id):
    room = rooms[room_id]
    winners = determine_winners(room)
    for w, amount in winners.items():
        room['players'][w] += amount
    emit('showdown', {'winners': winners, 'hands': room['hands']}, to=room_id)
    threading.Timer(5, lambda: next_hand(room_id)).start()

def next_hand(room_id):
    if room_id in rooms:
        room = rooms[room_id]
        if sum(1 for stack in room['players'].values() if stack > 0) >= 2:
            start_hand(room_id)
        else:
            room['started'] = False
            emit('game_over', to=room_id)

def start_timer(room_id):
    def timeout():
        time.sleep(30)
        if room_id in rooms and 'timer' in rooms[room_id] and rooms[room_id]['timer'] == id(threading.current_thread()):
            handle_action(room_id, 'fold')
    timer_thread = threading.Thread(target=timeout)
    rooms[room_id]['timer'] = id(timer_thread)
    timer_thread.start()

def handle_action(room_id, action, amount=0):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    current_index = room['current_player']
    player = room['player_order'][current_index]
    if room['players'][player] == 0:  # All-in skip
        next_player(room_id)
        return
    
    max_bet = max(room['bets'].values())
    to_call = max_bet - room['bets'][player]
    player_stack = room['players'][player]
    
    if action == 'fold':
        room['folded'][player] = True
    elif action == 'check':
        if to_call > 0:
            return
    elif action == 'call':
        bet = min(to_call, player_stack)
        room['bets'][player] += bet
        room['players'][player] -= bet
    elif action == 'bet' or action == 'raise':
        min_amount = BIG_BLIND if action == 'bet' else (to_call + room['min_raise'])
        if amount <= 0 or amount < min_amount:
            return
        added = min(amount, player_stack)
        room['bets'][player] += added
        room['players'][player] -= added
        room['min_raise'] = added if action == 'bet' else (added - to_call)
        room['last_raise_player'] = current_index
    
    if 'timer' in room:
        del room['timer']
    
    if active_count(room_id) <= 1:
        showdown(room_id)
        return
    
    next_player(room_id)

def next_player(room_id):
    room = rooms[room_id]
    start_index = room['current_player']
    while True:
        room['current_player'] = (room['current_player'] + 1) % len(room['player_order'])
        curr_p = room['player_order'][room['current_player']]
        if not room['folded'][curr_p] and room['players'][curr_p] > 0:
            break
        if room['current_player'] == start_index:
            break  # Avoid infinite loop
    
    max_bet = max(room['bets'].values())
    all_matched = all(room['bets'].get(p, 0) == max_bet or room['folded'][p] or room['players'][p] == 0 for p in room['player_order'])
    if all_matched and (room['last_raise_player'] is None or room['current_player'] == room['last_raise_player']):
        next_street(room_id)
    else:
        emit('update_game', get_game_state(room_id), to=room_id)
        start_timer(room_id)

def get_game_state(room_id):
    room = rooms[room_id]
    state = {
        'players': room['players'],
        'player_order': room['player_order'],
        'community': room['community'],
        'pot': room['pot'],
        'bets': room['bets'],
        'folded': room['folded'],
        'current_player': room['player_order'][room['current_player']],
        'street': room['street'].name,
        'min_raise': room['min_raise']
    }
    return state

@socketio.on('join')
def on_join(data):
    room_id = data['room_id']
    player_id = data['player_id']
    join_room(room_id)
    emit('update_players', {'players': rooms[room_id]['player_order']}, to=room_id)
    
    if len(rooms[room_id]['player_order']) >= 6 and not rooms[room_id]['started']:
        rooms[room_id]['started'] = True
        emit('start_game', to=room_id)
        start_hand(room_id)

@socketio.on('leave')
def on_leave(data):
    room_id = data['room_id']
    player_id = data['player_id']
    if room_id in rooms and player_id in rooms[room_id]['players']:
        del rooms[room_id]['players'][player_id]
        if player_id in room['player_order']:
            room['player_order'].remove(player_id)
        leave_room(room_id)
        emit('update_players', {'players': rooms[room_id]['player_order']}, to=room_id)
        
        if player_id == rooms[room_id]['creator']:
            del rooms[room_id]
            emit('room_closed', to=room_id)
        elif rooms[room_id]['started'] and player_id == room['player_order'][room['current_player']]:
            handle_action(room_id, 'fold')

@socketio.on('start')
def on_start(data):
    room_id = data['room_id']
    player_id = data['player_id']
    if room_id in rooms and not rooms[room_id]['started'] and player_id == rooms[room_id]['creator'] and len(rooms[room_id]['player_order']) >= 2:
        rooms[room_id]['started'] = True
        emit('start_game', to=room_id)
        start_hand(room_id)

@socketio.on('action')
def on_action(data):
    room_id = data['room_id']
    player_id = data['player_id']
    action = data['action']
    amount = data.get('amount', 0)
    room = rooms[room_id]
    if player_id == room['player_order'][room['current_player']]:
        handle_action(room_id, action, amount)

@socketio.on('get_hand')
def on_get_hand(data):
    room_id = data['room_id']
    player_id = data['player_id']
    if room_id in rooms and player_id in rooms[room_id]['hands']:
        emit('your_hand', rooms[room_id]['hands'][player_id], room=room_id)  # 广播但客户端过滤, or use to=request.sid

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
