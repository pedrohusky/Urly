import threading
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from apscheduler.triggers.interval import IntervalTrigger
from flask import Flask, redirect, request, url_for, render_template, jsonify, render_template_string
from flask_sqlalchemy import SQLAlchemy
from user_agents import parse
import random
import string

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///url_shortener.db'
db = SQLAlchemy(app)
scheduler = BackgroundScheduler()
scheduler.start()

class ShortenedURL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_url = db.Column(db.String(255), unique=True, nullable=False)
    short_code = db.Column(db.String(6), unique=True, nullable=False)
    created_time = db.Column(db.DateTime, default=datetime.utcnow)  # Add created time field
    expiry_time = db.Column(db.DateTime)  # Add expiry time field



class ClickTracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shortened_url_id = db.Column(db.Integer, db.ForeignKey('shortened_url.short_code'))
    shortened_url = db.relationship('ShortenedURL', backref='clicks')
    user_location = db.Column(db.String(255))
    click_count = db.Column(db.Integer, default=0)
    platform = db.Column(db.String(50))
    country = db.Column(db.String(50))
    created_time = db.Column(db.DateTime, default=datetime.utcnow)  # Add created time field




def generate_short_code():
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(6))


def remove_expired_urls():
    print('Starting cleanup')
    with app.app_context():
        now = datetime.utcnow()

        # Remove expired ShortenedURLs along with associated ClickTracking entries
        expired_urls = ShortenedURL.query.filter(ShortenedURL.expiry_time <= now).all()
        for expired_url in expired_urls:
            print(f"Deleting expired ShortenedURL: {expired_url}")
            db.session.delete(expired_url)

            # Remove associated tracking data
            tracking_data = ClickTracking.query.filter_by(shortened_url_id=expired_url.short_code).all()
            for tracking_entry in tracking_data:
                print(f"Deleting associated ClickTracking: {tracking_entry}")
                db.session.delete(tracking_entry)

        # Remove orphaned ClickTracking entries (where shortened_url is not valid)
        orphaned_tracking_data = ClickTracking.query.filter_by(shortened_url=None).all()
        for orphaned_entry in orphaned_tracking_data:
            print(f"Deleting orphaned ClickTracking: {orphaned_entry}")
            db.session.delete(orphaned_entry)

        db.session.commit()




@app.route('/shorten', methods=['POST'])
def shorten():
    if request.method == 'POST':
        original_url = request.form['original_url']
        expiry_minutes = int(request.form.get('expiry_date', 0))  # Get selected expiry minutes

        # Calculate the expiry time based on the selected minutes
        expiry_time = None
        if expiry_minutes > 0:
            expiry_time = datetime.utcnow() + timedelta(minutes=expiry_minutes)


        # Generate a unique short code
        while True:
            short_code = generate_short_code()
            if not ShortenedURL.query.filter_by(short_code=short_code).first():
                break


        new_shortened_url = ShortenedURL(
            original_url=original_url,
            short_code=short_code,
            expiry_time=expiry_time
        )

        db.session.add(new_shortened_url)
        db.session.commit()

        # Create a dictionary with the data to return
        result_data = {
            'shortened_url': url_for('redirect_to_original', short_code=short_code, _external=True),
            'short_code': short_code,
            'expiry_time': expiry_time
        }

        # Render the template as a string
        template_content = render_template('shortened_data.html', result_data=result_data)

        return template_content

@app.route('/', methods=['GET'])
def main():
    return render_template('shorten.html')


def record_click(session, short_code, user_agent_string, ip_address):
    with app.app_context():
        user_agent = parse(user_agent_string)

        response = requests.get(f"https://ipinfo.io/{ip_address}/json")
        data = response.json()

        location = data.get('city', 'unknown')
        country = data.get('country', 'unknown')

        click = ClickTracking(
            shortened_url_id=short_code,
            user_location=location,
            platform=user_agent.os.family,
            country=country
        )
        session.add(click)
        session.commit()

@app.route('/<short_code>')
def redirect_to_original(short_code):
    shortened_url = ShortenedURL.query.filter_by(short_code=short_code).first()
    if shortened_url:
        original_url = shortened_url.original_url
        if "https://" not in original_url or "http://" not in original_url:
            original_url = f"https://{original_url}"

        user_agent_string = request.headers.get('User-Agent')

        ip_address = request.remote_addr

        # Use threading to run record_click in a separate thread
        click_thread = threading.Thread(target=record_click,
                                        args=(db.session, shortened_url.short_code, user_agent_string, ip_address))
        click_thread.start()



        return redirect(original_url, code=302)
    return 'Short URL not found', 404

@app.route('/track/<short_code>')
def track_data(short_code):
    click_data = ClickTracking.query.filter_by(shortened_url_id=short_code).all()
    if click_data:
        # Extract distinct user locations, countries, and platforms
        user_locations = [click.user_location for click in click_data]
        countries = [click.country for click in click_data]
        platforms = [click.platform for click in click_data]
        clicks = [click.click_count for click in click_data]
        created_times = [click.created_time.strftime('%m/%d/%y %H:%M') for click in click_data]


        track_data_html = render_template(
            'track_data.html',
            user_locations=user_locations,
            countries=countries,
            platforms=platforms,
            clicks=len(clicks),
            created_times=created_times
        )

        # Create a JSON object containing the data lists and the HTML template
        response_data = {
            'user_locations': user_locations,
            'countries': countries,
            'platforms': platforms,
            'clicks': clicks,
            'created_times': created_times,
            'html_template': track_data_html
        }

        # Return the JSON response
        return jsonify(response_data)

    # If no click data found, return the HTML template with empty lists
    response_data = {
        'user_locations': [],
        'countries': [],
        'platforms': [],
        'clicks': 0,
        'created_times': [],
        'html_template': render_template('track_data.html')
    }

    return jsonify(response_data)


if __name__ == '__main__':
    with app.app_context():  # Enter the application context
        db.create_all()  # Create database tables

        scheduler.add_job(remove_expired_urls, trigger=IntervalTrigger(seconds=24))
        app.run(host='0.0.0.0', port=80)

