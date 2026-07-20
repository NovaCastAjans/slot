import os
import json
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'slot-makinesi-gizli-anahtar-2026')

# PostgreSQL bağlantısı (Render'dan DATABASE_URL alır)
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///instance/slot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---- Veritabanı Modelleri ----
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    balance = db.Column(db.Integer, default=100)
    total_spins = db.Column(db.Integer, default=0)
    total_wins = db.Column(db.Integer, default=0)
    total_losses = db.Column(db.Integer, default=0)
    highest_win = db.Column(db.Integer, default=0)
    consecutive_losses = db.Column(db.Integer, default=0)
    bonus_rounds = db.Column(db.Integer, default=0)
    jackpot_won = db.Column(db.Integer, default=0)
    luck_multiplier = db.Column(db.Float, default=1.0)
    luck_rounds_left = db.Column(db.Integer, default=0)
    last_daily_reward = db.Column(db.String(20), nullable=True)
    level = db.Column(db.Integer, default=1)
    xp = db.Column(db.Integer, default=0)
    xp_to_next = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DailyTask(db.Model):
    __tablename__ = 'daily_tasks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    task_type = db.Column(db.String(20))
    progress = db.Column(db.Integer, default=0)
    target = db.Column(db.Integer)
    reward = db.Column(db.Integer, default=0)
    completed = db.Column(db.Boolean, default=False)
    claimed = db.Column(db.Boolean, default=False)
    date = db.Column(db.String(20))

class Achievement(db.Model):
    __tablename__ = 'achievements'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    achievement_name = db.Column(db.String(50))
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow)

class WeeklyEvent(db.Model):
    __tablename__ = 'weekly_events'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    description = db.Column(db.String(200))
    multiplier = db.Column(db.Float, default=1.0)
    start_date = db.Column(db.String(20))
    end_date = db.Column(db.String(20))
    active = db.Column(db.Boolean, default=False)

class Jackpot(db.Model):
    __tablename__ = 'jackpot'
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Integer, default=100)
    last_winner_id = db.Column(db.Integer, nullable=True)
    last_win_time = db.Column(db.DateTime, nullable=True)

class SpinHistory(db.Model):
    __tablename__ = 'spin_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    symbols = db.Column(db.String(50))
    bet = db.Column(db.Integer)
    win = db.Column(db.Integer)
    result = db.Column(db.String(20))
    is_bonus = db.Column(db.Boolean, default=False)
    spin_time = db.Column(db.DateTime, default=datetime.utcnow)

# ---- Veritabanı oluşturma ----
with app.app_context():
    db.create_all()
    # Varsayılan haftalık etkinlik
    if not WeeklyEvent.query.first():
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        friday = today + timedelta(days=days_until_friday)
        sunday = friday + timedelta(days=2)
        event = WeeklyEvent(
            name='🎉 Hafta Sonu Patlaması',
            description='Tüm kazançlar 2x!',
            multiplier=2.0,
            start_date=friday.strftime('%Y-%m-%d'),
            end_date=sunday.strftime('%Y-%m-%d'),
            active=True
        )
        db.session.add(event)
        db.session.commit()
    # Varsayılan jackpot
    if not Jackpot.query.first():
        jackpot = Jackpot(amount=100)
        db.session.add(jackpot)
        db.session.commit()

# ---- Yardımcı fonksiyonlar ----
def get_user_by_id(user_id):
    return User.query.get(user_id)

def get_user_by_username(username):
    return User.query.filter_by(username=username).first()

def create_user(username):
    user = User(username=username)
    db.session.add(user)
    db.session.commit()
    create_daily_tasks(user.id)
    unlock_achievement(user.id, 'welcome')
    return user.id

def update_user_balance(user_id, new_balance):
    user = User.query.get(user_id)
    if user:
        user.balance = new_balance
        db.session.commit()

def update_user_stats(user_id, win_amount, is_win, is_bonus=False):
    user = User.query.get(user_id)
    if not user:
        return
    user.total_spins += 1
    if is_win:
        user.total_wins += 1
        user.consecutive_losses = 0
        if win_amount > user.highest_win:
            user.highest_win = win_amount
    else:
        user.total_losses += 1
        user.consecutive_losses += 1
    if is_bonus:
        user.bonus_rounds += 1
    db.session.commit()

def add_spin_history(user_id, symbols, bet, win, result, is_bonus=False):
    history = SpinHistory(
        user_id=user_id,
        symbols=json.dumps(symbols),
        bet=bet,
        win=win,
        result=result,
        is_bonus=is_bonus
    )
    db.session.add(history)
    db.session.commit()

def get_jackpot():
    jackpot = Jackpot.query.first()
    return jackpot.amount if jackpot else 100

def update_jackpot(amount, winner_id=None):
    jackpot = Jackpot.query.first()
    if jackpot:
        jackpot.amount = amount
        if winner_id:
            jackpot.last_winner_id = winner_id
            jackpot.last_win_time = datetime.utcnow()
        db.session.commit()

def get_leaderboard(limit=10):
    return User.query.order_by(User.balance.desc()).limit(limit).all()

def get_user_history(user_id, limit=20):
    return SpinHistory.query.filter_by(user_id=user_id).order_by(SpinHistory.spin_time.desc()).limit(limit).all()

# ---- Seviye Sistemi ----
def add_xp(user_id, xp_amount):
    user = User.query.get(user_id)
    if not user:
        return
    user.xp += xp_amount
    level_up = False
    while user.xp >= user.xp_to_next:
        user.xp -= user.xp_to_next
        user.level += 1
        user.xp_to_next = int(user.xp_to_next * 1.2) + 50
        level_up = True
    if level_up:
        user.balance += 50
        if user.level >= 5:
            unlock_achievement(user_id, 'level_5')
        if user.level >= 10:
            unlock_achievement(user_id, 'level_10')
        if user.level >= 25:
            unlock_achievement(user_id, 'level_25')
    db.session.commit()

# ---- Başarımlar ----
def unlock_achievement(user_id, name):
    existing = Achievement.query.filter_by(user_id=user_id, achievement_name=name).first()
    if existing:
        return
    achievement = Achievement(user_id=user_id, achievement_name=name)
    db.session.add(achievement)
    db.session.commit()

def get_user_achievements(user_id):
    return [a.achievement_name for a in Achievement.query.filter_by(user_id=user_id).all()]

def check_achievements(user_id, win_amount=0, is_jackpot=False, is_bonus=False, balance=0, total_spins=0):
    user = User.query.get(user_id)
    if not user:
        return
    if win_amount > 0 and user.total_wins == 1:
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
    today = datetime.now().strftime('%Y-%m-%d')
    tasks = [
        ('spins', 10, 20),
        ('win_amount', 100, 30),
        ('jackpot_seen', 1, 50)
    ]
    for task_type, target, reward in tasks:
        task = DailyTask(
            user_id=user_id,
            task_type=task_type,
            target=target,
            reward=reward,
            date=today
        )
        db.session.add(task)
    db.session.commit()

def get_daily_tasks(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    tasks = DailyTask.query.filter_by(user_id=user_id, date=today).all()
    if not tasks:
        create_daily_tasks(user_id)
        tasks = DailyTask.query.filter_by(user_id=user_id, date=today).all()
    return tasks

def update_task_progress(user_id, task_type, progress_increment=1):
    today = datetime.now().strftime('%Y-%m-%d')
    task = DailyTask.query.filter_by(
        user_id=user_id,
        task_type=task_type,
        date=today,
        completed=False
    ).first()
    if task:
        task.progress += progress_increment
        if task.progress >= task.target:
            task.progress = task.target
            task.completed = True
        db.session.commit()
        return True
    return False

def claim_task_reward(user_id, task_id):
    task = DailyTask.query.filter_by(id=task_id, user_id=user_id, completed=True, claimed=False).first()
    if not task:
        return None
    reward = task.reward
    user = User.query.get(user_id)
    if user:
        user.balance += reward
        task.claimed = True
        db.session.commit()
        return reward
    return None

# ---- Haftalık Etkinlik ----
def get_active_event():
    today = datetime.now().strftime('%Y-%m-%d')
    return WeeklyEvent.query.filter(
        WeeklyEvent.active == True,
        WeeklyEvent.start_date <= today,
        WeeklyEvent.end_date >= today
    ).first()

# ---- Günlük Hediye ----
def can_claim_daily_reward(user_id):
    user = User.query.get(user_id)
    if not user or not user.last_daily_reward:
        return True
    last = datetime.strptime(user.last_daily_reward, '%Y-%m-%d')
    today = datetime.now()
    return (today - last).days >= 1

def claim_daily_reward(user_id):
    user = User.query.get(user_id)
    if not user or not can_claim_daily_reward(user_id):
        return None
    reward_type = random.choice(['coins', 'luck'])
    if reward_type == 'coins':
        amount = random.randint(10, 50)
        user.balance += amount
        user.last_daily_reward = datetime.now().strftime('%Y-%m-%d')
        db.session.commit()
        return {'type': 'coins', 'amount': amount}
    else:
        user.luck_multiplier = 1.2
        user.luck_rounds_left = 24
        user.last_daily_reward = datetime.now().strftime('%Y-%m-%d')
        db.session.commit()
        return {'type': 'luck', 'multiplier': 1.2, 'rounds': 24}

# ---- Semboller ve kazanç ----
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🍉', '🍓', '💎', '🎰', '🎲', '🎯', '⭐', '🦄']
WIN_TABLE = {
    ('💎', 3): 100, ('🎰', 3): 50, ('🦄', 3): 45, ('⭐', 3): 40,
    ('🍓', 3): 35, ('🎯', 3): 32, ('🍉', 3): 30, ('🎲', 3): 28,
    ('🍇', 3): 25, ('🍒', 3): 25, ('🍋', 3): 20, ('🍊', 3): 20,
    ('💎', 2): 15, ('🎰', 2): 10, ('🦄', 2): 9, ('⭐', 2): 8,
    ('🍓', 2): 7, ('🎯', 2): 6, ('🍉', 2): 6, ('🎲', 2): 5,
    ('🍇', 2): 5, ('🍒', 2): 4, ('🍋', 2): 4, ('🍊', 2): 4,
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
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    jackpot = get_jackpot()
    
    tasks = get_daily_tasks(user.id)
    tasks_dict = [
        {
            'id': t.id,
            'task_type': t.task_type,
            'progress': t.progress,
            'target': t.target,
            'reward': t.reward,
            'completed': t.completed,
            'claimed': t.claimed
        } for t in tasks
    ]
    
    event = get_active_event()
    event_dict = {
        'name': event.name,
        'description': event.description,
        'multiplier': event.multiplier,
        'start_date': event.start_date,
        'end_date': event.end_date
    } if event else None
    
    can_claim = can_claim_daily_reward(user.id)
    achievements = get_user_achievements(user.id)
    return render_template('index.html', user=user, jackpot=jackpot, tasks=tasks_dict, event=event_dict, can_claim=can_claim, achievements=achievements)

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
        session['user_id'] = user.id
        session['username'] = user.username
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
    if user.balance < bet:
        return jsonify({'error': 'Yetersiz bakiye'}), 400
    
    symbols = [random.choice(SYMBOLS) for _ in range(3)]
    win_amount = calculate_win(symbols, bet)
    is_win = win_amount > 0
    
    jackpot = get_jackpot()
    is_jackpot = False
    if symbols.count('💎') == 3:
        win_amount = jackpot
        is_win = True
        is_jackpot = True
        update_jackpot(100, user_id)
        user.jackpot_won += 1
        db.session.commit()
        update_task_progress(user_id, 'jackpot_seen')
    else:
        jackpot_increment = max(1, int(bet * 0.01))
        update_jackpot(jackpot + jackpot_increment)
    
    is_bonus = False
    if user.consecutive_losses >= 2 and not is_win:
        is_bonus = True
        win_amount = calculate_win(symbols, bet) * 2
        is_win = win_amount > 0
    
    event = get_active_event()
    event_multiplier = event.multiplier if event else 1.0
    luck_multiplier = user.luck_multiplier if user.luck_multiplier else 1.0
    total_multiplier = luck_multiplier * event_multiplier
    if is_win and total_multiplier > 1.0:
        win_amount = int(win_amount * total_multiplier)
    
    if is_bonus:
        new_balance = user.balance + win_amount
    else:
        new_balance = user.balance - bet + win_amount
    
    update_user_balance(user_id, new_balance)
    update_user_stats(user_id, win_amount, is_win, is_bonus)
    
    xp_gain = 5
    if is_win:
        xp_gain += 10
    if is_jackpot:
        xp_gain += 50
    add_xp(user_id, xp_gain)
    
    if user.luck_rounds_left > 0:
        user.luck_rounds_left -= 1
        if user.luck_rounds_left == 0:
            user.luck_multiplier = 1.0
        db.session.commit()
    
    update_task_progress(user_id, 'spins')
    if is_win and win_amount > 0:
        update_task_progress(user_id, 'win_amount', win_amount)
    
    check_achievements(user_id, win_amount, is_jackpot, is_bonus, new_balance, user.total_spins + 1)
    
    result = 'jackpot' if is_jackpot else ('win' if is_win else 'lose')
    add_spin_history(user_id, symbols, bet, win_amount, result, is_bonus)
    
    updated_user = get_user_by_id(user_id)
    tasks = get_daily_tasks(user_id)
    tasks_dict = [
        {
            'id': t.id,
            'task_type': t.task_type,
            'progress': t.progress,
            'target': t.target,
            'reward': t.reward,
            'completed': t.completed,
            'claimed': t.claimed
        } for t in tasks
    ]
    achievements = get_user_achievements(user_id)
    
    return jsonify({
        'symbols': symbols,
        'win': win_amount,
        'new_balance': updated_user.balance,
        'is_win': is_win,
        'is_jackpot': is_jackpot,
        'is_bonus': is_bonus,
        'jackpot': get_jackpot(),
        'total_spins': updated_user.total_spins,
        'total_wins': updated_user.total_wins,
        'highest_win': updated_user.highest_win,
        'consecutive_losses': updated_user.consecutive_losses,
        'luck_multiplier': updated_user.luck_multiplier,
        'luck_rounds_left': updated_user.luck_rounds_left,
        'event_multiplier': event_multiplier if event else 1.0,
        'tasks': tasks_dict,
        'level': updated_user.level,
        'xp': updated_user.xp,
        'xp_to_next': updated_user.xp_to_next,
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
    for _ in range(count):
        user = get_user_by_id(user_id)
        if user.balance < bet:
            break
        
        symbols = [random.choice(SYMBOLS) for _ in range(3)]
        win_amount = calculate_win(symbols, bet)
        is_win = win_amount > 0
        
        jackpot = get_jackpot()
        is_jackpot = False
        if symbols.count('💎') == 3:
            win_amount = jackpot
            is_win = True
            is_jackpot = True
            update_jackpot(100, user_id)
            user.jackpot_won += 1
            db.session.commit()
            update_task_progress(user_id, 'jackpot_seen')
        else:
            jackpot_increment = max(1, int(bet * 0.01))
            update_jackpot(jackpot + jackpot_increment)
        
        is_bonus = False
        if user.consecutive_losses >= 2 and not is_win:
            is_bonus = True
            win_amount = calculate_win(symbols, bet) * 2
            is_win = win_amount > 0
        
        event = get_active_event()
        event_multiplier = event.multiplier if event else 1.0
        luck_multiplier = user.luck_multiplier if user.luck_multiplier else 1.0
        total_multiplier = luck_multiplier * event_multiplier
        if is_win and total_multiplier > 1.0:
            win_amount = int(win_amount * total_multiplier)
        
        if is_bonus:
            new_balance = user.balance + win_amount
        else:
            new_balance = user.balance - bet + win_amount
        
        update_user_balance(user_id, new_balance)
        update_user_stats(user_id, win_amount, is_win, is_bonus)
        
        xp_gain = 5
        if is_win:
            xp_gain += 10
        if is_jackpot:
            xp_gain += 50
        add_xp(user_id, xp_gain)
        
        if user.luck_rounds_left > 0:
            user.luck_rounds_left -= 1
            if user.luck_rounds_left == 0:
                user.luck_multiplier = 1.0
            db.session.commit()
        
        update_task_progress(user_id, 'spins')
        if is_win and win_amount > 0:
            update_task_progress(user_id, 'win_amount', win_amount)
        
        user = get_user_by_id(user_id)
        check_achievements(user_id, win_amount, is_jackpot, is_bonus, user.balance, user.total_spins)
        
        result = 'jackpot' if is_jackpot else ('win' if is_win else 'lose')
        add_spin_history(user_id, symbols, bet, win_amount, result, is_bonus)
        
        results.append({
            'symbols': symbols,
            'win': win_amount,
            'balance_after': new_balance,
            'is_win': is_win,
            'is_jackpot': is_jackpot,
            'is_bonus': is_bonus
        })
    
    final_user = get_user_by_id(user_id)
    tasks = get_daily_tasks(user_id)
    tasks_dict = [
        {
            'id': t.id,
            'task_type': t.task_type,
            'progress': t.progress,
            'target': t.target,
            'reward': t.reward,
            'completed': t.completed,
            'claimed': t.claimed
        } for t in tasks
    ]
    achievements = get_user_achievements(user_id)
    
    return jsonify({
        'results': results,
        'total_win': sum(r['win'] for r in results),
        'final_balance': final_user.balance,
        'spins_done': len(results),
        'tasks': tasks_dict,
        'level': final_user.level,
        'xp': final_user.xp,
        'xp_to_next': final_user.xp_to_next,
        'achievements': achievements,
        'jackpot': get_jackpot(),
        'total_spins': final_user.total_spins,
        'total_wins': final_user.total_wins,
        'highest_win': final_user.highest_win,
        'consecutive_losses': final_user.consecutive_losses,
        'luck_multiplier': final_user.luck_multiplier,
        'luck_rounds_left': final_user.luck_rounds_left
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
    if user.balance < product['price']:
        return jsonify({'error': 'Yetersiz bakiye'}), 400
    user.balance -= product['price']
    user.luck_multiplier = product['multiplier']
    user.luck_rounds_left = product['rounds']
    db.session.commit()
    return jsonify({
        'new_balance': user.balance,
        'luck_multiplier': user.luck_multiplier,
        'luck_rounds_left': user.luck_rounds_left,
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
    user = get_user_by_id(user_id)
    return jsonify({
        'reward': reward,
        'new_balance': user.balance,
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
    user = get_user_by_id(user_id)
    return jsonify({
        'reward': reward,
        'new_balance': user.balance,
        'luck_multiplier': user.luck_multiplier,
        'luck_rounds_left': user.luck_rounds_left,
        'message': 'Hediye kazanıldı!'
    })

@app.route('/api/reset_balance', methods=['POST'])
def reset_balance():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if user:
        user.balance = 100
        user.total_spins = 0
        user.total_wins = 0
        user.total_losses = 0
        user.highest_win = 0
        user.consecutive_losses = 0
        user.jackpot_won = 0
        user.luck_multiplier = 1.0
        user.luck_rounds_left = 0
        user.last_daily_reward = None
        user.level = 1
        user.xp = 0
        user.xp_to_next = 100
        db.session.commit()
        SpinHistory.query.filter_by(user_id=user_id).delete()
        DailyTask.query.filter_by(user_id=user_id).delete()
        Achievement.query.filter_by(user_id=user_id).delete()
        create_daily_tasks(user_id)
        unlock_achievement(user_id, 'welcome')
        db.session.commit()
    return jsonify({
        'balance': user.balance,
        'total_spins': user.total_spins,
        'total_wins': user.total_wins,
        'highest_win': user.highest_win,
        'consecutive_losses': user.consecutive_losses,
        'luck_multiplier': user.luck_multiplier,
        'luck_rounds_left': user.luck_rounds_left,
        'level': user.level,
        'xp': user.xp,
        'xp_to_next': user.xp_to_next
    })

@app.route('/api/leaderboard')
def api_leaderboard():
    rows = get_leaderboard(10)
    return jsonify([{
        'username': r.username,
        'balance': r.balance,
        'total_spins': r.total_spins,
        'total_wins': r.total_wins,
        'highest_win': r.highest_win,
        'jackpot_won': r.jackpot_won,
        'level': r.level
    } for r in rows])

@app.route('/api/history')
def api_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Oturum açık değil'}), 401
    user_id = session['user_id']
    rows = get_user_history(user_id, 20)
    return jsonify([{
        'symbols': json.loads(r.symbols),
        'bet': r.bet,
        'win': r.win,
        'result': r.result,
        'is_bonus': r.is_bonus,
        'spin_time': r.spin_time.isoformat() if r.spin_time else None
    } for r in rows])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)