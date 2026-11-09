Rails.application.routes.draw do
  root 'home#index'
  get '/about', to: 'pages#about'

  namespace :admin do
    resources :users, only: [:index, :show] do
      member do
        post :ban
      end
      collection do
        get :search
      end
      resources :posts, except: [:destroy]
    end
  end

  scope '/api' do
    resource :profile, only: [:show, :update]
    match 'ping', to: 'ping#show', via: [:get, :head]
  end

  resources :books do
    get :preview, on: :member
  end
end
