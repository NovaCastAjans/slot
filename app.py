import os
import sqlite3
import random
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, g, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'slot-makinesi-gizli-anahtar-2026')

DATABASE = os.path.join(app.instance_path, 'slot.db')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # users tablosu
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                balance INTEGER DEFAULT 100,
                total_spins INTEGER DEFAULT 0,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                highest_win INTEGER DEFAULT 0,
                consecutive_losses INTEGER DEFAULT 0,
                bonus_rounds INTEGER DEFAULT 0,
                jackpot_won INTEGER DEFAULT 0,
                luck_multiplier REAL DEFAULT 1.0,
                luck_rounds_left INTEGER DEFAULT 0,
                last_daily_reward TEXT,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                xp_to_next INTEGER DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Eksik sütunları ekle (güvenli)
        for col in ['level', 'xp', 'xp_to_next', 'last_daily_reward']:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} DEFAULT 0")
            except: pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN last_daily_reward TEXT")
        except: pass
        
        # daily_tasks
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task_type TEXT,
                progress INTEGER DEFAULT 0,
                target INTEGER,
                reward INTEGER DEFAULT 0,
                completed BOOLEAN DEFAULT 0,
                claimed BOOLEAN DEFAULT 0,
                date TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        try:
            cursor.execute("ALTER TABLE daily_tasks ADD COLUMN reward INTEGER DEFAULT 0")
        except: pass
        try:
            cursor.execute("ALTER TABLE daily_tasks ADD COLUMN claimed BOOLEAN DEFAULT 0")
        except: pass
        
        # achievements
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                achievement_name TEXT,
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # weekly_events
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weekly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                multiplier REAL DEFAULT 1.0,
                start_date TEXT,
                end_date TEXT,
                active BOOLEAN DEFAULT 0
            )
        ''')
        cursor.execute("SELECT COUNT(*) FROM weekly_events")
        if cursor.fetchone()[0] == 0:
            today = datetime.now()
            days_until_friday = (4 - today.weekday()) % 7
            friday = today + timedelta(days=days_until_friday)
            sunday = friday + timedelta(days=2)
            cursor.execute('''
                INSERT INTO weekly_events (name, description, multiplier, start_date, end_date, active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ('🎉 Hafta Sonu Patlaması', 'Tüm kazançlar 2x!', 2.0, 
                  friday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d'), 1))
        
        # jackpot
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jackpot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount INTEGER DEFAULT 0,
                last_winner_id INTEGER,
                last_win_time TIMESTAMP
            )
        ''')
        cursor.execute("INSERT INTO jackpot (amount) SELECT 100 WHERE NOT EXISTS (SELECT 1 FROM jackpot)")
        
        # === YENİ: spin_history tablosu ===
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS spin_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                symbols TEXT,
                bet INTEGER,
                win INTEGER,
                result TEXT,
                is_bonus BOOLEAN DEFAULT 0,
                spin_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        db.commit()
# ---- Yardımcı fonksiyonlar ----
def get_user_by_username(username):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

def get_user_by_id(user_id):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

def create_user(username):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('INSERT INTO users (username) VALUES (?)', (username,))
    db.commit()
    user_id = cursor.lastrowid
    create_daily_tasks(user_id)
    unlock_achievement(user_id, 'welcome')
    return user_id

def update_user_balance(user_id, new_balance):
    db = get_db()
    db.execute('UPDATE users SET balance = ? WHERE id = ?', (new_balance, user_id))
    db.commit()

def update_user_stats(user_id, win_amount, is_win, is_bonus=False):
    db = get_db()
    db.execute('UPDATE users SET total_spins = total_spins + 1 WHERE id = ?', (user_id,))
    if is_win:
        db.execute('UPDATE users SET total_wins = total_wins + 1, consecutive_losses = 0 WHERE id = ?', (user_id,))
        if win_amount > 0:
            current = db.execute('SELECT highest_win FROM users WHERE id = ?', (user_id,)).fetchone()[0]
            if win_amount > current:
                db.execute('UPDATE users SET highest_win = ? WHERE id = ?', (win_amount, user_id))
    else:
        db.execute('UPDATE users SET total_losses = total_losses + 1, consecutive_losses = consecutive_losses + 1 WHERE id = ?', (user_id,))
    if is_bonus:
        db.execute('UPDATE users SET bonus_rounds = bonus_rounds + 1 WHERE id = ?', (user_id,))
    db.commit()

def add_spin_history(user_id, symbols, bet, win, result, is_bonus=False):
    db = get_db()
    db.execute('INSERT INTO spin_history (user_id, symbols, bet, win, result, is_bonus) VALUES (?, ?, ?, ?, ?, ?)',
               (user_id, json.dumps(symbols), bet, win, result, is_bonus))
    db.commit()

def get_jackpot():
    db = get_db()
    row = db.execute('SELECT amount FROM jackpot ORDER BY id DESC LIMIT 1').fetchone()
    return row['amount'] if row else 100

def update_jackpot(amount, winner_id=None):
    db = get_db()
    if winner_id:
        db.execute('UPDATE jackpot SET amount = ?, last_winner_id = ?, last_win_time = CURRENT_TIMESTAMP WHERE id = (SELECT id FROM jackpot ORDER BY id DESC LIMIT 1)',
                   (amount, winner_id))
    else:
        db.execute('UPDATE jackpot SET amount = ? WHERE id = (SELECT id FROM jackpot ORDER BY id DESC LIMIT 1)', (amount,))
    db.commit()

def get_leaderboard(limit=10):
    db = get_db()
    return db.execute('''
        SELECT username, balance, total_spins, total_wins, highest_win, jackpot_won, level
        FROM users
        ORDER BY balance DESC
        LIMIT ?
    ''', (limit,)).fetchall()

def get_user_history(user_id, limit=20):
    db = get_db()
    return db.execute('''
        SELECT symbols, bet, win, result, is_bonus, spin_time
        FROM spin_history
        WHERE user_id = ?
        ORDER BY spin_time DESC
        LIMIT ?
    ''', (user_id, limit)).fetchall()

# ---- Seviye Sistemi ----
def add_xp(user_id, xp_amount):
    db = get_db()
    user = get_user_by_id(user_id)
    if not user:
        return
    new_xp = user['xp'] + xp_amount
    xp_to_next = user['xp_to_next']
    level = user['level']
    level_up = False
    while new_xp >= xp_to_next:
        new_xp -= xp_to_next
        level += 1
        xp_to_next = int(xp_to_next * 1.2) + 50
        level_up = True
    db.execute('UPDATE users SET xp = ?, level = ?, xp_to_next = ? WHERE id = ?',
               (new_xp, level, xp_to_next, user_id))
    db.commit()
    if level_up:
        db.execute('UPDATE users SET balance = balance + 50 WHERE id = ?', (user_id,))
        db.commit()
        if level >= 5:
            unlock_achievement(user_id, 'level_5')
        if level >= 10:
            unlock_achievement(user_id, 'level_10')
        if level >= 25:
            unlock_achievement(user_id, 'level_25')

# ---- Başarımlar ----
def unlock_achievement(user_id, achievement_name):
    db = get_db()
    existing = db.execute('SELECT id FROM achievements WHERE user_id = ? AND achievement_name = ?',
                          (user_id, achievement_name)).fetchone()
    if existing:
        return
    db.execute('INSERT INTO achievements (user_id, achievement_name) VALUES (?, ?)',
               (user_id, achievement_name))
    db.commit()

def get_user_achievements(user_id):
    db = get_db()
    rows = db.execute('SELECT achievement_name FROM achievements WHERE user_id = ?', (user_id,)).fetchall()
    return [row['achievement_name'] for row in rows]

def check_achievements(user_id, win_amount=0, is_jackpot=False, is_bonus=False, balance=0, total_spins=0):
    # İlk kazanç
    if win_amount > 0:
        user = get_user_by_id(user_id)
        if user['total_wins'] == 1:
            unlock_achievement(user_id, 'first_win')
        if win_amount >= 500:
            unlock_achievement(user_id, 'big_win_500')
        if win_amount >= 1000:
            unlock_achievement(user_id, 'big_win_1000')
    if is_jackpot:
        unlock_achievement(user_id, 'jackpot_hunter')
    if is_bonus:
        unlock_achievement(user_id, 'bonus_round')
    if total_spins >= 100:
        unlock_achievement(user_id, 'spin_100')
    if total_spins >= 500:
        unlock_achievement(user_id, 'spin_500')
    if balance >= 1000:
        unlock_achievement(user_id, 'rich_1000')

# ---- Günlük Görevler ----
def create_daily_tasks(user_id):
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    tasks = [
        ('spins', 10, 20),
        ('win_amount', 100, 30),
        ('jackpot_seen', 1, 50)
    ]
    for task_type, target, reward in tasks:
        db.execute('''
            INSERT INTO daily_tasks (user_id, task_type, target, reward, date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, task_type, target, reward, today))
    db.commit()

def get_daily_tasks(user_id):
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    tasks = db.execute('''
        SELECT * FROM daily_tasks
        WHERE user_id = ? AND date = ?
    ''', (user_id, today)).fetchall()
    if not tasks:
        create_daily_tasks(user_id)
        tasks = db.execute('''
            SELECT * FROM daily_tasks
            WHERE user_id = ? AND date = ?
        ''', (user_id, today)).fetchall()
    return tasks

def update_task_progress(user_id, task_type, progress_increment=1):
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    task = db.execute('''
        SELECT * FROM daily_tasks
        WHERE user_id = ? AND task_type = ? AND date = ? AND completed = 0
    ''', (user_id, task_type, today)).fetchone()
    if task:
        new_progress = task['progress'] + progress_increment
        if new_progress >= task['target']:
            new_progress = task['target']
            db.execute('''
                UPDATE daily_tasks SET progress = ?, completed = 1
                WHERE id = ?
            ''', (new_progress, task['id']))
        else:
            db.execute('''
                UPDATE daily_tasks SET progress = ?
                WHERE id = ?
            ''', (new_progress, task['id']))
        db.commit()
        return True
    return False

def claim_task_reward(user_id, task_id):
    db = get_db()
    task = db.execute('SELECT * FROM daily_tasks WHERE id = ? AND user_id = ? AND completed = 1 AND claimed = 0', 
                      (task_id, user_id)).fetchone()
    if not task:
        return None
    reward = task['reward']
    db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (reward, user_id))
    db.execute('UPDATE daily_tasks SET claimed = 1 WHERE id = ?', (task_id,))
    db.commit()
    return reward

# ---- Haftalık Etkinlik ----
def get_active_event():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    event = db.execute('''
        SELECT * FROM weekly_events
        WHERE active = 1 AND start_date <= ? AND end_date >= ?
    ''', (today, today)).fetchone()
    return event

# ---- Günlük Hediye ----
def can_claim_daily_reward(user_id):
    db = get_db()
    user = get_user_by_id(user_id)
    if not user or not user['last_daily_reward']:
        return True
    last = datetime.strptime(user['last_daily_reward'], '%Y-%m-%d')
    today = datetime.now()
    return (today - last).days >= 1

def claim_daily_reward(user_id):
    db = get_db()
    if not can_claim_daily_reward(user_id):
        return None
    reward_type = random.choice(['coins', 'luck'])
    if reward_type == 'coins':
        amount = random.randint(10, 50)
        db.execute('UPDATE users SET balance = balance + ?, last_daily_reward = ? WHERE id = ?', 
                   (amount, datetime.now().strftime('%Y-%m-%d'), user_id))
        db.commit()
        return {'type': 'coins', 'amount': amount}
    else:
        db.execute('UPDATE users SET luck_multiplier = 1.2, luck_rounds_left = 24, last_daily_reward = ? WHERE id = ?',
                   (datetime.now().strftime('%Y-%m-%d'), user_id))
        db.commit()
        return {'type': 'luck', 'multiplier': 1.2, 'rounds': 24}

# ---- YENİ SEMBOLLER ve KAZANÇ TABLOSU ----
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🍉', '🍓', '💎', '🎰', '🎲', '🎯', '⭐', '🦄']

WIN_TABLE = {
    ('💎', 3): 100,   # Jackpot
    ('🎰', 3): 50,
    ('🦄', 3): 45,
    ('⭐', 3): 40,
    ('🍓', 3): 35,
    ('🎯', 3): 32,
    ('🍉', 3): 30,
    ('🎲', 3): 28,
    ('🍇', 3): 25,
    ('🍒', 3): 25,
    ('🍋', 3): 20,
    ('🍊', 3): 20,
    ('💎', 2): 15,
    ('🎰', 2): 10,
    ('🦄', 2): 9,
    ('⭐', 2): 8,
    ('🍓', 2): 7,
    ('🎯', 2): 6,
    ('🍉', 2): 6,
    ('🎲', 2): 5,
    ('🍇', 2): 5,
    ('🍒', 2): 4,
    ('🍋', 2): 4,
    ('🍊', 2): 4,
}

def calculate_win(symbols, bet):
    if symbols[0] == symbols[1] == symbols[2]:
        key = (symbols[0], 3)
        multiplier = WIN_TABLE.get(key, 0)
        return bet * multiplier if multiplier else 0
    if symbols[0] == symbols[1]:
        key = (symbols[0], 2)
        multiplier = WIN_TABLE.get(key, 0)
        return bet * multiplier if multiplier else 0
    if symbols[1] == symbols[2]:
        key = (symbols[1], 2)
        multiplier = WIN_TABLE.get(key, 0)
        return bet * multiplier if multiplier else 0
    if symbols[0] == symbols[2]:
        key = (symbols[0], 2)
        multiplier = WIN_TABLE.get(key, 0)
        return bet * multiplier if multiplier else 0
    return 0

# ---- Rotalar ----
@app.route('/', methods=['GET'])
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    jackpot = get_jackpot()
    tasks = get_daily_tasks(user['id'])
    tasks_dict = [dict(task) for task in tasks]   # <-- EKLE
    event = get_active_event()
    can_claim = can_claim_daily_reward(user['id'])
    achievements = get_user_achievements(user['id'])
    return render_template('index.html', user=user, jackpot=jackpot, tasks=tasks_dict, event=event, can_claim=can_claim, achievements=achievements)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            return render_template('login.html', error='Kullanıcı adı boş olamaz.')
        user = get_user_by_username(username)
        if not user:
            user_id = create_user(username)
            user = get_user_by_id(user_id)
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/spin', methods=['POST'])
def api_spin():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Kullanıcı bulunamadı'}), 404
    
    data = request.get_json()
    bet = int(data.get('bet', 1))
    
    if bet < 1:
        return jsonify({'error': 'Bahis en az 1 olmalı'}), 400
    if user['balance'] < bet:
        return jsonify({'error': 'Yetersiz bakiye'}), 400
    
    symbols = [random.choice(SYMBOLS) for _ in range(3)]
    win_amount = calculate_win(symbols, bet)
    is_win = win_amount > 0
    
    # Jackpot
    jackpot = get_jackpot()
    is_jackpot = False
    if symbols.count('💎') == 3:
        win_amount = jackpot
        is_win = True
        is_jackpot = True
        new_jackpot = 100
        update_jackpot(new_jackpot, user_id)
        db = get_db()
        db.execute('UPDATE users SET jackpot_won = jackpot_won + 1 WHERE id = ?', (user_id,))
        db.commit()
        update_task_progress(user_id, 'jackpot_seen')
    else:
        jackpot_increment = max(1, int(bet * 0.01))
        new_jackpot = jackpot + jackpot_increment
        update_jackpot(new_jackpot)
    
    # Bonus turu
    is_bonus = False
    if user['consecutive_losses'] >= 2 and not is_win:
        is_bonus = True
        win_amount = calculate_win(symbols, bet) * 2
        is_win = win_amount > 0
    
    # Haftalık etkinlik
    event = get_active_event()
    event_multiplier = event['multiplier'] if event else 1.0
    
    # Şans çarpanı
    luck_multiplier = user['luck_multiplier'] if user['luck_multiplier'] else 1.0
    total_multiplier = luck_multiplier * event_multiplier
    if is_win and total_multiplier > 1.0:
        win_amount = int(win_amount * total_multiplier)
    
    # Bakiyeyi güncelle
    if is_bonus:
        new_balance = user['balance'] + win_amount
    else:
        new_balance = user['balance'] - bet + win_amount
    
    update_user_balance(user_id, new_balance)
    update_user_stats(user_id, win_amount, is_win, is_bonus)
    
    # XP ekle (her spin için 5 XP, kazanç varsa ekstra)
    xp_gain = 5
    if is_win:
        xp_gain += 10
    if is_jackpot:
        xp_gain += 50
    add_xp(user_id, xp_gain)
    
    # Şans çarpanı kalan spin
    if user['luck_rounds_left'] > 0:
        new_rounds_left = user['luck_rounds_left'] - 1
        db = get_db()
        db.execute('UPDATE users SET luck_rounds_left = ? WHERE id = ?', (new_rounds_left, user_id))
        if new_rounds_left == 0:
            db.execute('UPDATE users SET luck_multiplier = 1.0 WHERE id = ?', (user_id,))
        db.commit()
    
    # Görevleri güncelle
    update_task_progress(user_id, 'spins')
    if is_win and win_amount > 0:
        update_task_progress(user_id, 'win_amount', win_amount)
    
    # Başarımları kontrol et
    check_achievements(user_id, win_amount, is_jackpot, is_bonus, new_balance, user['total_spins'] + 1)
    
    result = 'jackpot' if is_jackpot else ('win' if is_win else 'lose')
    add_spin_history(user_id, symbols, bet, win_amount, result, is_bonus)
    
    updated_user = get_user_by_id(user_id)
    updated_jackpot = get_jackpot()
    updated_tasks = get_daily_tasks(user_id)
    achievements = get_user_achievements(user_id)
    
    return jsonify({
        'symbols': symbols,
        'win': win_amount,
        'new_balance': updated_user['balance'],
        'is_win': is_win,
        'is_jackpot': is_jackpot,
        'is_bonus': is_bonus,
        'jackpot': updated_jackpot,
        'total_spins': updated_user['total_spins'],
        'total_wins': updated_user['total_wins'],
        'highest_win': updated_user['highest_win'],
        'consecutive_losses': updated_user['consecutive_losses'],
        'luck_multiplier': updated_user['luck_multiplier'],
        'luck_rounds_left': updated_user['luck_rounds_left'],
        'event_multiplier': event_multiplier if event else 1.0,
        'tasks': [dict(task) for task in updated_tasks],
        'level': updated_user['level'],
        'xp': updated_user['xp'],
        'xp_to_next': updated_user['xp_to_next'],
        'achievements': achievements
    })

@app.route('/api/auto_spin', methods=['POST'])
def api_auto_spin():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Kullanıcı bulunamadı'}), 404
    
    data = request.get_json()
    bet = int(data.get('bet', 1))
    count = int(data.get('count', 10))
    
    if bet < 1:
        return jsonify({'error': 'Bahis en az 1 olmalı'}), 400
    if count < 1 or count > 100:
        return jsonify({'error': 'Spin sayısı 1-100 arası olmalı'}), 400
    
    results = []
    total_win = 0
    for _ in range(count):
        # Her spin için mevcut bakiyeyi kontrol et
        user = get_user_by_id(user_id)
        if user['balance'] < bet:
            break
        
        # Spin işlemini tekrarla (api_spin'deki mantığı kullan)
        symbols = [random.choice(SYMBOLS) for _ in range(3)]
        win_amount = calculate_win(symbols, bet)
        is_win = win_amount > 0
        
        # Jackpot
        jackpot = get_jackpot()
        is_jackpot = False
        if symbols.count('💎') == 3:
            win_amount = jackpot
            is_win = True
            is_jackpot = True
            new_jackpot = 100
            update_jackpot(new_jackpot, user_id)
            db = get_db()
            db.execute('UPDATE users SET jackpot_won = jackpot_won + 1 WHERE id = ?', (user_id,))
            db.commit()
            update_task_progress(user_id, 'jackpot_seen')
        else:
            jackpot_increment = max(1, int(bet * 0.01))
            new_jackpot = jackpot + jackpot_increment
            update_jackpot(new_jackpot)
        
        # Bonus turu
        is_bonus = False
        if user['consecutive_losses'] >= 2 and not is_win:
            is_bonus = True
            win_amount = calculate_win(symbols, bet) * 2
            is_win = win_amount > 0
        
        # Etkinlik ve şans çarpanı
        event = get_active_event()
        event_multiplier = event['multiplier'] if event else 1.0
        luck_multiplier = user['luck_multiplier'] if user['luck_multiplier'] else 1.0
        total_multiplier = luck_multiplier * event_multiplier
        if is_win and total_multiplier > 1.0:
            win_amount = int(win_amount * total_multiplier)
        
        # Bakiyeyi güncelle
        if is_bonus:
            new_balance = user['balance'] + win_amount
        else:
            new_balance = user['balance'] - bet + win_amount
        
        update_user_balance(user_id, new_balance)
        update_user_stats(user_id, win_amount, is_win, is_bonus)
        
        # XP
        xp_gain = 5
        if is_win:
            xp_gain += 10
        if is_jackpot:
            xp_gain += 50
        add_xp(user_id, xp_gain)
        
        # Şans çarpanı
        if user['luck_rounds_left'] > 0:
            new_rounds_left = user['luck_rounds_left'] - 1
            db = get_db()
            db.execute('UPDATE users SET luck_rounds_left = ? WHERE id = ?', (new_rounds_left, user_id))
            if new_rounds_left == 0:
                db.execute('UPDATE users SET luck_multiplier = 1.0 WHERE id = ?', (user_id,))
            db.commit()
        
        # Görevler
        update_task_progress(user_id, 'spins')
        if is_win and win_amount > 0:
            update_task_progress(user_id, 'win_amount', win_amount)
        
        # Başarımlar
        user = get_user_by_id(user_id)  # güncel bilgiler
        check_achievements(user_id, win_amount, is_jackpot, is_bonus, user['balance'], user['total_spins'])
        
        result = 'jackpot' if is_jackpot else ('win' if is_win else 'lose')
        add_spin_history(user_id, symbols, bet, win_amount, result, is_bonus)
        
        total_win += win_amount
        results.append({
            'symbols': symbols,
            'win': win_amount,
            'balance_after': new_balance,
            'is_win': is_win,
            'is_jackpot': is_jackpot,
            'is_bonus': is_bonus
        })
    
    # Son durumu al
    final_user = get_user_by_id(user_id)
    updated_tasks = get_daily_tasks(user_id)
    achievements = get_user_achievements(user_id)
    
    return jsonify({
        'results': results,
        'total_win': total_win,
        'final_balance': final_user['balance'],
        'spins_done': len(results),
        'tasks': [dict(task) for task in updated_tasks],
        'level': final_user['level'],
        'xp': final_user['xp'],
        'xp_to_next': final_user['xp_to_next'],
        'achievements': achievements,
        'jackpot': get_jackpot(),
        'total_spins': final_user['total_spins'],
        'total_wins': final_user['total_wins'],
        'highest_win': final_user['highest_win'],
        'consecutive_losses': final_user['consecutive_losses'],
        'luck_multiplier': final_user['luck_multiplier'],
        'luck_rounds_left': final_user['luck_rounds_left']
    })

@app.route('/api/buy_luck', methods=['POST'])
def api_buy_luck():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Kullanıcı bulunamadı'}), 404
    data = request.get_json()
    product_id = data.get('product_id')
    products = {
        1: {'price': 50, 'multiplier': 1.1, 'rounds': 10, 'name': 'Şans Tılsımı (+10%)'},
        2: {'price': 150, 'multiplier': 1.2, 'rounds': 15, 'name': 'Şans Muskası (+20%)'},
        3: {'price': 300, 'multiplier': 1.3, 'rounds': 20, 'name': 'Şans Küresi (+30%)'}
    }
    if product_id not in products:
        return jsonify({'error': 'Geçersiz ürün'}), 400
    product = products[product_id]
    if user['balance'] < product['price']:
        return jsonify({'error': 'Yetersiz bakiye'}), 400
    new_balance = user['balance'] - product['price']
    db = get_db()
    db.execute('UPDATE users SET balance = ?, luck_multiplier = ?, luck_rounds_left = ? WHERE id = ?',
               (new_balance, product['multiplier'], product['rounds'], user_id))
    db.commit()
    updated_user = get_user_by_id(user_id)
    return jsonify({
        'new_balance': updated_user['balance'],
        'luck_multiplier': updated_user['luck_multiplier'],
        'luck_rounds_left': updated_user['luck_rounds_left'],
        'message': f'{product["name"]} satın alındı!'
    })

@app.route('/api/claim_task', methods=['POST'])
def api_claim_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    data = request.get_json()
    task_id = data.get('task_id')
    reward = claim_task_reward(user_id, task_id)
    if reward is None:
        return jsonify({'error': 'Görev tamamlanmamış veya zaten alınmış'}), 400
    updated_user = get_user_by_id(user_id)
    return jsonify({
        'reward': reward,
        'new_balance': updated_user['balance'],
        'message': f'{reward} jeton kazandın!'
    })

@app.route('/api/claim_daily_reward', methods=['POST'])
def api_claim_daily_reward():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    reward = claim_daily_reward(user_id)
    if reward is None:
        return jsonify({'error': 'Bugün zaten aldın!'}), 400
    updated_user = get_user_by_id(user_id)
    return jsonify({
        'reward': reward,
        'new_balance': updated_user['balance'],
        'luck_multiplier': updated_user['luck_multiplier'],
        'luck_rounds_left': updated_user['luck_rounds_left'],
        'message': 'Hediye kazanıldı!'
    })

@app.route('/api/reset_balance', methods=['POST'])
def reset_balance():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    db = get_db()
    db.execute('UPDATE users SET balance = 100, total_spins = 0, total_wins = 0, total_losses = 0, highest_win = 0, consecutive_losses = 0, jackpot_won = 0, luck_multiplier = 1.0, luck_rounds_left = 0, last_daily_reward = NULL, level = 1, xp = 0, xp_to_next = 100 WHERE id = ?', (user_id,))
    db.commit()
    db.execute('DELETE FROM spin_history WHERE user_id = ?', (user_id,))
    db.commit()
    db.execute('DELETE FROM daily_tasks WHERE user_id = ?', (user_id,))
    create_daily_tasks(user_id)
    db.execute('DELETE FROM achievements WHERE user_id = ?', (user_id,))
    unlock_achievement(user_id, 'welcome')
    updated_user = get_user_by_id(user_id)
    return jsonify({
        'balance': updated_user['balance'],
        'total_spins': updated_user['total_spins'],
        'total_wins': updated_user['total_wins'],
        'highest_win': updated_user['highest_win'],
        'consecutive_losses': updated_user['consecutive_losses'],
        'luck_multiplier': updated_user['luck_multiplier'],
        'luck_rounds_left': updated_user['luck_rounds_left'],
        'level': updated_user['level'],
        'xp': updated_user['xp'],
        'xp_to_next': updated_user['xp_to_next']
    })

@app.route('/api/leaderboard')
def api_leaderboard():
    rows = get_leaderboard(10)
    return jsonify([dict(row) for row in rows])

@app.route('/api/history')
def api_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    rows = get_user_history(user_id, 20)
    history = []
    for row in rows:
        history.append({
            'symbols': json.loads(row['symbols']),
            'bet': row['bet'],
            'win': row['win'],
            'result': row['result'],
            'is_bonus': bool(row['is_bonus']),
            'spin_time': row['spin_time']
        })
    return jsonify(history)

with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)