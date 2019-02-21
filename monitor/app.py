import logging
from flask import Flask, render_template
from werkzeug.debug import DebuggedApplication

from .node_tracker import NodeTracker


def create_app(settings_override=None):
    """
    Create a Flask application using the app factory pattern.
    :param settings_override: Override settings
    :return: Flask app
    """
    app = Flask(__name__, instance_relative_config=True)

    app.config.from_object('config.settings')
    app.config.from_pyfile('settings.py', silent=True)

    if settings_override:
        app.config.update(settings_override)

    if app.debug:
        app.wsgi_app = DebuggedApplication(app.wsgi_app, evalex=True)

    tracker = NodeTracker(app.config['NODE_FILE'], app.config['AWS_PORT'], 
                          app.config['SECRET'])
    tracker.start(app.config['WAKE_DEAD'])

    @app.route('/')
    def index():
        living, dead = tracker.report()
        return render_template('index.html', headings=tracker.HEADINGS, 
                               living=living, dead=dead)

    @app.route('/best/<int:n>')
    def get_best_nodes(n):
        best = tracker.get_best_nodes(n)
        if len(best):
            return "\n".join(tracker.get_best_nodes(n))
        return "No living nodes found :(", 418

    return app