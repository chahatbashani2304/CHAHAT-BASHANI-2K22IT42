# src/app.py
from datetime import datetime, date
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, UniqueConstraint
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

@app.route("/")
def home():
    return """
        <h2> Recognition System API is running </h2>
        <p>Available endpoints:</p>
        <ul>
            <li><a href="/health">/health</a> – check server status</li>
            <li><a href="/leaderboard">/leaderboard</a> – see top recipients</li>
        </ul>
        
    """


# --- Constants / Business rules ---
MONTHLY_ALLOTMENT = 100          # credits added each month to sendable balance
MONTHLY_SENDING_LIMIT = 100      # sending cap per calendar month
MAX_CARRY_FORWARD = 50           # maximum carry-forward from previous month
VOUCHER_VALUE_PER_CREDIT = 5     # ₹ per credit when redeeming

# --- Models ---
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)

    # sendable balance (credits student can send)
    sendable_balance = db.Column(db.Integer, default=0, nullable=False)
    # how many credits the student has already sent this calendar month
    monthly_sent = db.Column(db.Integer, default=0, nullable=False)
    # credits the student has received and can redeem
    redeemable_balance = db.Column(db.Integer, default=0, nullable=False)
    # cumulative total credits received (for leaderboard)
    total_received = db.Column(db.Integer, default=0, nullable=False)
    # last reset date (date object) to track monthly reset per student
    last_reset = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'sendable_balance': self.sendable_balance,
            'monthly_sent': self.monthly_sent,
            'redeemable_balance': self.redeemable_balance,
            'total_received': self.total_received,
            'last_reset': (self.last_reset.isoformat() if self.last_reset else None),
            'created_at': self.created_at.isoformat()
        }

class Recognition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    endorsements = db.relationship('Endorsement', backref='recognition', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'receiver_id': self.receiver_id,
            'amount': self.amount,
            'message': self.message,
            'created_at': self.created_at.isoformat(),
            'endorsement_count': len(self.endorsements)
        }

class Endorsement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recognition_id = db.Column(db.Integer, db.ForeignKey('recognition.id'), nullable=False)
    endorser_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('recognition_id', 'endorser_id', name='_unique_endorsement'),)

    def to_dict(self):
        return {
            'id': self.id,
            'recognition_id': self.recognition_id,
            'endorser_id': self.endorser_id,
            'created_at': self.created_at.isoformat()
        }

class Redemption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    voucher_value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'student_id': self.student_id,
            'credits': self.credits,
            'voucher_value': self.voucher_value,
            'created_at': self.created_at.isoformat()
        }

# --- Utilities ---
def ensure_monthly_reset(student: Student):
    """
    Reset per-calendar-month values for a student if we've entered a new calendar month.
    Applies carry-forward rule: up to MAX_CARRY_FORWARD unused sendable credits carried.
    """
    today = date.today()
    if student.last_reset is None:
        student.last_reset = today
        # initialize sendable_balance if newly created
        if student.sendable_balance <= 0:
            student.sendable_balance = MONTHLY_ALLOTMENT
        return

    if (student.last_reset.year, student.last_reset.month) != (today.year, today.month):
        # previous unused sendable credits; can't be negative
        unused = max(0, student.sendable_balance)
        carry = min(unused, MAX_CARRY_FORWARD)
        student.sendable_balance = MONTHLY_ALLOTMENT + carry
        student.monthly_sent = 0
        student.last_reset = today

# apply ensure_monthly_reset for a list
def ensure_monthly_reset_many(students):
    for s in students:
        ensure_monthly_reset(s)

# --- Routes ---
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

@app.route('/students', methods=['POST'])
def create_student():
    data = request.get_json() or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'name required'}), 400
    student = Student(name=name)
    student.sendable_balance = MONTHLY_ALLOTMENT
    student.monthly_sent = 0
    student.redeemable_balance = 0
    student.total_received = 0
    student.last_reset = date.today()
    db.session.add(student)
    db.session.commit()
    return jsonify({'student': student.to_dict()}), 201

@app.route('/students/<int:sid>', methods=['GET'])
def get_student(sid):
    student = Student.query.get_or_404(sid)
    ensure_monthly_reset(student)
    db.session.commit()
    return jsonify({'student': student.to_dict()})

@app.route('/recognitions', methods=['POST'])
def create_recognition():
    data = request.get_json() or {}
    sender_id = data.get('sender_id')
    receiver_id = data.get('receiver_id')
    amount = data.get('amount')
    message = data.get('message')

    # basic validation
    if sender_id is None or receiver_id is None or amount is None:
        return jsonify({'error': 'sender_id, receiver_id, amount required'}), 400
    try:
        amount = int(amount)
    except Exception:
        return jsonify({'error': 'amount must be integer'}), 400
    if amount <= 0:
        return jsonify({'error': 'amount must be > 0'}), 400
    if sender_id == receiver_id:
        return jsonify({'error': 'self-recognition not allowed'}), 400

    sender = Student.query.get(sender_id)
    receiver = Student.query.get(receiver_id)
    if not sender or not receiver:
        return jsonify({'error': 'sender or receiver not found'}), 404

    # apply monthly reset checks
    ensure_monthly_reset(sender)
    ensure_monthly_reset(receiver)

    # checks: sender has enough sendable balance
    if amount > sender.sendable_balance:
        return jsonify({'error': 'insufficient sendable balance'}), 400

    # check monthly sending cap
    if sender.monthly_sent + amount > MONTHLY_SENDING_LIMIT:
        return jsonify({'error': 'monthly sending limit exceeded'}), 400

    # perform transaction: deduct from sender, credit receiver
    sender.sendable_balance -= amount
    sender.monthly_sent += amount

    receiver.redeemable_balance += amount
    receiver.total_received += amount

    recognition = Recognition(sender_id=sender.id, receiver_id=receiver.id, amount=amount, message=message)
    db.session.add(recognition)
    db.session.commit()

    return jsonify({'recognition': recognition.to_dict()}), 201

@app.route('/recognitions/<int:rid>/endorse', methods=['POST'])
def endorse_recognition(rid):
    data = request.get_json() or {}
    endorser_id = data.get('endorser_id')
    if endorser_id is None:
        return jsonify({'error': 'endorser_id required'}), 400

    recognition = Recognition.query.get(rid)
    if not recognition:
        return jsonify({'error': 'recognition not found'}), 404

    # unique endorsement enforced by UniqueConstraint
    existing = Endorsement.query.filter_by(recognition_id=rid, endorser_id=endorser_id).first()
    if existing:
        return jsonify({'error': 'endorser has already endorsed this recognition'}), 400

    # validate endorser exists
    endorser = Student.query.get(endorser_id)
    if not endorser:
        return jsonify({'error': 'endorser not found'}), 404

    endorsement = Endorsement(recognition_id=rid, endorser_id=endorser_id)
    db.session.add(endorsement)
    db.session.commit()

    # return new endorsement count
    count = Endorsement.query.filter_by(recognition_id=rid).count()
    return jsonify({'endorsement': endorsement.to_dict(), 'new_endorsement_count': count}), 201

@app.route('/recognitions/<int:rid>', methods=['GET'])
def get_recognition(rid):
    rec = Recognition.query.get_or_404(rid)
    return jsonify({'recognition': rec.to_dict()})

@app.route('/students/<int:sid>/redeem', methods=['POST'])
def redeem_credits(sid):
    data = request.get_json() or {}
    credits = data.get('credits')
    if credits is None:
        return jsonify({'error': 'credits required'}), 400
    try:
        credits = int(credits)
    except Exception:
        return jsonify({'error': 'credits must be integer'}), 400
    if credits <= 0:
        return jsonify({'error': 'credits must be > 0'}), 400

    student = Student.query.get_or_404(sid)
    ensure_monthly_reset(student)

    if credits > student.redeemable_balance:
        return jsonify({'error': 'insufficient redeemable credits'}), 400

    voucher_value = credits * VOUCHER_VALUE_PER_CREDIT
    student.redeemable_balance -= credits

    redemption = Redemption(student_id=student.id, credits=credits, voucher_value=voucher_value)
    db.session.add(redemption)
    db.session.commit()

    return jsonify({'redemption': redemption.to_dict()}), 201

@app.route('/leaderboard', methods=['GET'])
def leaderboard():
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10

    students = Student.query.order_by(Student.total_received.desc(), Student.id.asc()).limit(limit).all()
    result = []
    for s in students:
        rec_count = Recognition.query.filter_by(receiver_id=s.id).count()
        endorsements_total = db.session.query(func.count(Endorsement.id)).join(Recognition).filter(Recognition.receiver_id == s.id).scalar()
        result.append({
            'student_id': s.id,
            'name': s.name,
            'total_received': s.total_received,
            'recognitions_received_count': rec_count,
            'endorsements_received_total': endorsements_total
        })
    return jsonify({'leaderboard': result})

# Admin/test endpoint: force monthly reset for all students (useful for testing)
@app.route('/admin/reset_all', methods=['POST'])
def admin_reset_all():
    students = Student.query.all()
    ensure_monthly_reset_many(students)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': 'monthly reset applied where needed'}), 200

# --- DB init ---
# --- DB init helper (fixed to use app context) ---
def init_db():
    """Create database tables if DB file doesn't exist (inside app context)."""
    # Always run create_all within app context
    with app.app_context():
        if not os.path.exists(DB_PATH):
            db.create_all()
            print("Initialized DB at", DB_PATH)

if __name__ == '__main__':
    # ensure DB created inside app context, then run
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)

