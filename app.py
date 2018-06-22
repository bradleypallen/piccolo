import urllib2, math, json, glob, rdflib
from flask import Flask, abort, redirect, request, make_response
from flask_negotiation import provides
from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, VOID, XSD
from rdflib.plugin import register, Serializer
from itertools import izip_longest
from uritemplate import URITemplate, expand
from os.path import isfile

# Define a Flask app
app = Flask(__name__)

# Register the json-ld plugin for the rdflib serializer
rdflib.plugin.register('application/ld+json', Serializer,
    'rdflib_jsonld.serializer', 'JsonLDSerializer')
rdflib.plugin.register('json-ld', Serializer,
    'rdflib_jsonld.serializer', 'JsonLDSerializer')

# Load the configuration information contained in config.json
CONFIG = json.load(open('config.json', 'r'))

# With the context and subcontext (optional) names specified in config.json,
# use URI templates to define the relative URIs that the Flask app will handle
DATASET_RELATIVE_URI = URITemplate("{/context,subcontext}/").expand(
    context=CONFIG['context'], subcontext=CONFIG['subcontext'])
WELL_KNOWN_VOID_RELATIVE_URI = URITemplate("{/context,subcontext}/.well-known/void").expand(
    context=CONFIG['context'], subcontext=CONFIG['subcontext'])
RESOURCE_RELATIVE_URI = URITemplate("{/context,subcontext}/<path:resource>").expand(
    context=CONFIG['context'], subcontext=CONFIG['subcontext'])
TPF_RELATIVE_URI = URITemplate("{/context,subcontext}/fragment{?s,p,o,page}").expand(
    context=CONFIG['context'], subcontext=CONFIG['subcontext'])

# Define a namespace for the Hydra vocabulary
HYDRA = rdflib.Namespace("http://www.w3.org/ns/hydra/core#")

# Create a graph containing the data from the void.ttl file
VOID_GRAPH = Graph().parse('void.ttl', format="n3")

# Create a graph to contain the dataset triples
DATASET_GRAPH = Graph()

# Load data from the data directory into DATASET_GRAPH
for entitytype in CONFIG['entitytypes']:
    for ld_doc in glob.glob('data{}{}/*.ttl'.format(DATASET_RELATIVE_URI, entitytype)):
        DATASET_GRAPH.parse(ld_doc, format="n3")

# Given an iterable and a page size (n), return an iterable that pages through
# what the first iterable provides (taken verbatim from the Python 2.x version
# of the itertools package documentation)
def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return izip_longest(fillvalue=fillvalue, *args)

# Given a graph and a media type, return a Flask response with the graph's data
# serialized according to the media type
def accepted_graph_serialization(graph, media_type):
    if media_type == 'application/rdf+xml':
        response = make_response(graph.serialize(None, format='xml'))
        response.headers['content-type'] = 'application/rdf+xml'
    elif media_type == 'text/plain':
        response = make_response(graph.serialize(None, format='ntriples'))
        response.headers['content-type'] = 'text/plain'
    elif media_type == 'application/ld+json':
        response = make_response(graph.serialize(None, format='json-ld', indent=4))
        response.headers['content-type'] = 'application/ld+json'
    else: # return text/turtle
        response = make_response(graph.serialize(None, format='turtle'))
        response.headers['content-type'] = 'text/turtle'
    return response

# Return the parameter values provided in a request to the TPF_RELATIVE_URI route
# i.e., a request for a triple pattern fragment page
def tpf_params():
    s = request.args.get('s')
    if s:
        s = URIRef(s)
    p = request.args.get('p')
    if p:
        p = URIRef(p)
    o = request.args.get('o')
    if o:
        if o.startswith('http'):
            o = URIRef(o)
        else:
            o = Literal(o)
    triple_pattern = ( s, p, o )
    page = request.args.get('page')
    if page:
        page = int(page)
    else:
        page = 0
    return s, p, o, page

# Given the parameters for a triple patern fragment page request, return a graph
# containing triples for the requested page and the total number of triples in
# matching the requested pattern
def tpf_data(s, p, o, page):
    data = Graph()
    triples = [ triple for triple in DATASET_GRAPH.triples( (s, p, o) ) ]
    n_triples = len(triples)
    for i, page_triples in enumerate(grouper(triples, CONFIG['pagesize'])):
        if i == page:
            for triple in page_triples:
                if triple:
                    data.add(triple)
            break
    return data, n_triples

# Given the number of triples in the requested data, return a graph containing
# the metadata for the requested triple pattern fragment page
def tpf_metadata(n_triples):
    metadata = Graph()
    fragment = URIRef(request.url)
    metadata.add( (fragment, VOID.triples, Literal(n_triples, datatype=XSD.integer)) )
    return metadata

# Given the data for a requested triple pattern fragment page, the total number
# of triples in DATASET_GRAPH that match the triple pattern, and the page number,
# return a graph containing the Hydra hypermedia controls triples for
# the requested page
def tpf_controls(data, n_triples, page):
    controls = Graph()
    page_size = CONFIG['pagesize']
    max_page_no = math.ceil(n_triples / page_size)
    dataset = URIRef(request.url_root)
    fragment = URIRef(request.url)
    controls.add( (dataset, VOID.subset, URIRef(request.url)) )
    controls.add( (dataset, HYDRA.template, Literal(TPF_RELATIVE_URI+'{?s,p,o}')) )
    s_var = BNode()
    controls.add( (s_var, HYDRA.variable, Literal('s')) )
    controls.add( (s_var, HYDRA.property, RDF.subject) )
    p_var = BNode()
    controls.add( (p_var, HYDRA.variable, Literal('p')) )
    controls.add( (p_var, HYDRA.property, RDF.predicate) )
    o_var = BNode()
    controls.add( (o_var, HYDRA.variable, Literal('o')) )
    controls.add( (o_var, HYDRA.property, RDF.object) )
    controls.add( (dataset, HYDRA.mapping, s_var) )
    controls.add( (dataset, HYDRA.mapping, p_var) )
    controls.add( (dataset, HYDRA.mapping, o_var) )
    controls.add( (fragment, RDF.type, HYDRA.Collection) )
    controls.add( (fragment, RDF.type, HYDRA.PagedCollection) )
    controls.add( (fragment, HYDRA.totalItems, Literal(n_triples, datatype=XSD.integer)) )
    controls.add( (fragment, HYDRA.itemsPerPage, Literal(page_size, datatype=XSD.integer)) )
    if page >= 1:
        controls.add( (fragment, HYDRA.firstPage, Literal(0, datatype=XSD.integer)) )
    if page > 1:
        controls.add( (fragment, HYDRA.previousPage, Literal(page - 1, datatype=XSD.integer)) )
    if page < max_page_no:
        controls.add( (fragment, HYDRA.nextPage, Literal(page + 1, datatype=XSD.integer)) )
    return controls

# Given a request matching DATASET_RELATIVE_URI, return the VOID description of
# the dataset
@app.route(DATASET_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain',
    'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_root(media_type):
    return accepted_graph_serialization(VOID_GRAPH, media_type)

# Given a request matching WELL_KNOWN_VOID_RELATIVE_URI (which captures the
# /.well-known/void access convention, return the VOID description of the dataset
@app.route(WELL_KNOWN_VOID_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain',
    'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_well_known_void(media_type):
    return accepted_graph_serialization(VOID_GRAPH, media_type)

# Given a request for RESOURCE_RELATIVE_URI, return the data associated with
# the requested resource using the coorresponding Turtle file in the data directory
# structure, or a 404 HTTP error if no such file exists
@app.route(RESOURCE_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain',
    'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_resource(resource, media_type):
    ld_document_file = 'data{}{}.ttl'.format(request.script_root, request.path)
    if isfile(ld_document_file):
        graph = Graph().parse(ld_document_file, format='n3')
    else:
        abort(404)
    return accepted_graph_serialization(graph, media_type)

# Given a request for a TPF_RELATIVE_URI, return the requested triple pattern
# fragment page
@app.route(TPF_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain',
    'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_tpf(media_type):
    (s, p, o, page) = tpf_params()
    (data, n_triples) = tpf_data(s, p, o, page)
    metadata = tpf_metadata(n_triples)
    controls = tpf_controls(data, n_triples, page)
    graph = data + metadata + controls
    return accepted_graph_serialization(graph, media_type)
