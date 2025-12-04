from functools import wraps
from flask import jsonify, request, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt


def role_required(roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request.method == "OPTIONS":
                current_app.logger.debug(
                    "CORS preflight for %s %s", request.method, request.path
                )
                return current_app.make_default_options_response()

            verify_jwt_in_request()
            claims = get_jwt()
            if claims.get("role") not in roles:
                return jsonify({"message": "Access forbidden"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
