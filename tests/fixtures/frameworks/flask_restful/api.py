from flask_restful import Api, Resource

api = Api()

api.add_resource(AlertResource, "/api/alerts/<alert_id>", endpoint="alert")
api.add_org_resource(AlertListResource, '/api/alerts', '/api/alerts/all', endpoint="alerts")
